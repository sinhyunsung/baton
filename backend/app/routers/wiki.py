import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Claim, ClaimWikiLink, LintIssue, RawSource, WikiPage, WikiVersion

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


@router.get("/tree")
def wiki_tree(db: Session = Depends(get_session)):
    pages = db.scalars(select(WikiPage).order_by(WikiPage.folder_path, WikiPage.title)).all()
    out = []
    for p in pages:
        n_claims = db.scalar(
            select(func.count()).select_from(ClaimWikiLink).where(ClaimWikiLink.page_id == p.id)
        )
        n_confirmed = db.scalar(
            select(func.count()).select_from(ClaimWikiLink)
            .join(Claim, Claim.id == ClaimWikiLink.claim_id)
            .where(ClaimWikiLink.page_id == p.id, Claim.human_confirmed.is_(True))
        )
        n_lint = db.scalar(
            select(func.count()).select_from(LintIssue)
            .where(LintIssue.page_id == p.id, LintIssue.status == "open")
        )
        out.append({
            "id": p.id, "title": p.title, "folder_path": p.folder_path,
            "page_type": p.page_type,
            "updated_at": p.updated_at.isoformat(),
            "claims": n_claims, "confirmed": n_confirmed, "lint_open": n_lint,
        })
    return {"pages": out}


WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


@router.get("/graph")
def wiki_graph(focus: str = "", db: Session = Depends(get_session)):
    """옵시디언식 관계 그래프.
    노드: 위키 페이지(+ focus 시 해당 클레임들). 엣지:
    - link   : 본문 [[내부 링크]]가 실제 페이지 제목과 일치
    - source : 같은 원본 문서에서 나온 클레임을 근거로 쓰는 페이지끼리
    - claim  : 클레임 ↔ 소속 페이지 (focus 모드에서만)
    """
    pages = db.scalars(select(WikiPage)).all()
    title2id = {p.title: p.id for p in pages}
    claim_counts = dict(db.execute(
        select(ClaimWikiLink.page_id, func.count()).group_by(ClaimWikiLink.page_id)).all())

    nodes = [{
        "id": f"p{p.id}", "kind": "page", "ref": p.id, "label": p.title,
        "group": (p.folder_path or "").split("/")[0] or "공통",
        "size": 10 + min(claim_counts.get(p.id, 0), 18) * 0.8,
    } for p in pages]
    edges, seen = [], set()

    def add_edge(a: str, b: str, kind: str):
        key = (min(a, b), max(a, b), kind)
        if a != b and key not in seen:
            seen.add(key)
            edges.append({"a": a, "b": b, "kind": kind})

    for p in pages:
        for t in WIKI_LINK_RE.findall(p.body or ""):
            tid = title2id.get(t.strip())
            if tid:
                add_edge(f"p{p.id}", f"p{tid}", "link")

    rows = db.execute(
        select(ClaimWikiLink.page_id, Claim.source_id)
        .join(Claim, Claim.id == ClaimWikiLink.claim_id)).all()
    by_src: dict[int, set[int]] = {}
    for pid, sid in rows:
        by_src.setdefault(sid, set()).add(pid)
    for pids in by_src.values():
        ordered = sorted(pids)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                add_edge(f"p{ordered[i]}", f"p{ordered[j]}", "source")

    # 클레임 노드는 전체 그래프에도 상시 포함 (페이지당 상한으로 과밀 방지)
    node_ids = {n["id"] for n in nodes}

    def add_page_claims(pid: int, cap: int = 30):
        links = db.scalars(select(ClaimWikiLink).where(ClaimWikiLink.page_id == pid).limit(cap)).all()
        for l in links:
            c = db.get(Claim, l.claim_id)
            if not c:
                continue
            cid = f"c{c.id}"
            if cid not in node_ids:
                node_ids.add(cid)
                nodes.append({"id": cid, "kind": "claim", "ref": c.id,
                              "label": (c.object or c.subject or "")[:26],
                              "group": "클레임", "size": 6.5,
                              "confirmed": c.human_confirmed})
            add_edge(cid, f"p{pid}", "claim")

    for p in pages:
        add_page_claims(p.id)

    return {"nodes": nodes, "edges": edges, "focus": focus}


@router.get("/pages/{page_id}")
def wiki_page(page_id: int, db: Session = Depends(get_session)):
    p = db.get(WikiPage, page_id)
    if not p:
        raise HTTPException(404)
    links = db.scalars(select(ClaimWikiLink).where(ClaimWikiLink.page_id == page_id)).all()
    claims = []
    for l in links:
        c = db.get(Claim, l.claim_id)
        if not c:
            continue
        src = db.get(RawSource, c.source_id)
        claims.append({
            "id": c.id, "text": c.rendered_text, "claim_type": c.claim_type,
            "subject": c.subject, "predicate": c.predicate, "object": c.object,
            "polarity": c.polarity, "owner": c.owner, "project": c.project,
            "scope": c.scope, "time_scope": c.time_scope,
            "confidence": c.confidence, "human_confirmed": c.human_confirmed,
            "superseded": c.superseded,
            "source": {"id": src.id, "type": src.source_type, "title": src.title, "path": src.path},
        })
    issues = db.scalars(
        select(LintIssue).where(LintIssue.page_id == page_id, LintIssue.status == "open")
    ).all()
    # 본문 [[링크]] → 실제 페이지 id 해석 + 이 페이지를 참조하는 백링크
    all_pages = db.scalars(select(WikiPage)).all()
    title2id = {ap.title: ap.id for ap in all_pages}
    links = {t.strip(): title2id.get(t.strip())
             for t in WIKI_LINK_RE.findall(p.body or "")}
    backlinks = [{"id": ap.id, "title": ap.title}
                 for ap in all_pages if ap.id != p.id
                 and p.title in WIKI_LINK_RE.findall(ap.body or "")]
    return {
        "id": p.id, "title": p.title, "folder_path": p.folder_path,
        "page_type": p.page_type, "body": p.body,
        "updated_at": p.updated_at.isoformat(), "claims": claims,
        "links": links, "backlinks": backlinks,
        "open_issues": [{"id": i.id, "type": i.issue_type, "question": i.question} for i in issues],
    }


@router.get("/pages/{page_id}/versions")
def wiki_versions(page_id: int, db: Session = Depends(get_session)):
    rows = db.scalars(
        select(WikiVersion).where(WikiVersion.page_id == page_id).order_by(WikiVersion.id.desc())
    ).all()
    return {"versions": [
        {"id": v.id, "cause": v.cause, "created_at": v.created_at.isoformat(), "body": v.body}
        for v in rows
    ]}
