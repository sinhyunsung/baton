"""위키 갱신 적용. LLM이 결정한 갱신(create/update)을 즉시 위키에 반영한다 (버전 스냅샷 보존)."""
import json
import logging
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..config import SIM_WIKI_MATCH
from ..models import Claim, ClaimWikiLink, RawSource, WikiPage, WikiVersion
from ..prompts.wiki import WIKI_DECIDE_SYSTEM, WIKI_DECIDE_USER

log = logging.getLogger("baton.wiki")

WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_SECTION_MARK = "\n## 관련 페이지"
_SKIP_SEG = {"공통", "담당자미정"}


def _clean_title(title: str, folder_path: str) -> str:
    """제목 앞에 붙은 상위 폴더명 접두어("프로젝트명 - 주제" 등)를 제거한다."""
    t = (title or "").strip()
    parts = [seg for seg in (folder_path or "").split("/") if seg]
    changed = True
    while changed:
        changed = False
        for seg in parts:
            for sep in (" - ", " – ", " — ", ": ", " · "):
                pre = seg + sep
                if t.startswith(pre) and len(t) > len(pre) + 1:
                    t = t[len(pre):].strip()
                    changed = True
    return t or title


def _strip_related_section(body: str) -> str:
    return (body or "").split(_SECTION_MARK)[0].rstrip()


def _with_related_section(db: Session, page: WikiPage, base_body: str) -> str:
    """같은 프로젝트·같은 담당자 페이지로 '관련 페이지' 섹션을 결정적으로 생성해 붙인다.
    (LLM이 [[링크]]를 빠뜨려도 위키 연결성이 보장되도록 시스템이 직접 잇는다)"""
    parts = [seg for seg in (page.folder_path or "").split("/") if seg]
    proj = parts[1] if len(parts) >= 2 and parts[1] not in _SKIP_SEG else None
    own = parts[2] if len(parts) >= 3 and parts[2] not in _SKIP_SEG else None
    inline = {t.strip() for t in WIKI_LINK_RE.findall(base_body)}
    rel: list[tuple[str, str]] = []
    seen = {page.title} | inline
    for p2 in db.scalars(select(WikiPage).where(WikiPage.id != page.id)):
        p2p = [seg for seg in (p2.folder_path or "").split("/") if seg]
        reason = None
        if proj and len(p2p) >= 2 and p2p[1] == proj:
            reason = "같은 프로젝트"
        elif own and len(p2p) >= 3 and p2p[2] == own:
            reason = "같은 담당자"
        if reason and p2.title not in seen:
            seen.add(p2.title)
            rel.append((p2.title, reason))
    rel.sort(key=lambda x: x[1] != "같은 프로젝트")  # 프로젝트 우선
    if not rel:
        return base_body
    lines = "\n".join(f"- [[{t}]] ({r})" for t, r in rel[:6])
    return f"{base_body}\n{_SECTION_MARK}\n{lines}\n"


def refresh_all_links(db: Session) -> int:
    """전체 페이지의 제목 접두어 정리 + 관련 페이지 섹션 재생성 (LLM 없음, updated_at 미변경).
    1패스에서 제목을 전부 정리한 뒤 2패스에서 섹션을 만든다 — 섹션이 옛 제목을 참조하지 않도록."""
    pages = db.scalars(select(WikiPage)).all()
    changed = 0
    for page in pages:
        new_title = _clean_title(page.title, page.folder_path)
        if new_title != page.title:
            page.title = new_title
            changed += 1
    db.flush()
    for page in pages:
        new_body = _with_related_section(db, page, _strip_related_section(page.body))
        if new_body != page.body:
            page.body = new_body
            changed += 1
    if changed:
        db.commit()
    return changed


def find_candidate_pages(db: Session, claims: list[Claim], k: int = 5) -> list[WikiPage]:
    """새 클레임들과 유사한 기존 클레임 → 연결된 위키 페이지 후보."""
    page_ids: dict[int, float] = {}
    for c in claims:
        rows = db.execute(
            select(Claim.id, Claim.embedding.cosine_distance(c.embedding).label("d"))
            .where(Claim.id != (c.id or -1), Claim.embedding.isnot(None))
            .order_by("d").limit(5)
        ).all()
        for cid, dist in rows:
            sim = 1 - dist
            if sim < SIM_WIKI_MATCH:
                continue
            for link in db.scalars(select(ClaimWikiLink).where(ClaimWikiLink.claim_id == cid)):
                page_ids[link.page_id] = max(page_ids.get(link.page_id, 0), sim)
    top = sorted(page_ids.items(), key=lambda x: -x[1])[:k]
    return [db.get(WikiPage, pid) for pid, _ in top if db.get(WikiPage, pid)]


def apply_wiki_updates(db: Session, src: RawSource, claims: list[Claim]):
    """클레임을 위키에 즉시 반영: LLM이 create/update를 결정하면 바로 적용한다."""
    candidates = find_candidate_pages(db, claims)
    claims_json = json.dumps(
        [{"id": c.id, "text": c.rendered_text, "claim_type": c.claim_type,
          "owner": c.owner, "project": c.project,
          "confidence": c.confidence} for c in claims], ensure_ascii=False, indent=1)
    candidates_json = json.dumps(
        [{"id": p.id, "title": p.title, "folder_path": p.folder_path, "body": p.body[:3000]}
         for p in candidates], ensure_ascii=False, indent=1) or "[]"

    all_titles = "\n".join(
        f"- [[{p.title}]] ({p.folder_path})"
        for p in db.scalars(select(WikiPage).order_by(WikiPage.updated_at.desc()).limit(150))
    ) or "(아직 페이지 없음)"
    result = llm.extract_json(
        WIKI_DECIDE_SYSTEM,
        WIKI_DECIDE_USER.format(
            source_desc=f"{src.source_type}/{src.path} — {src.title}",
            claims_json=claims_json, candidates_json=candidates_json,
            all_titles=all_titles,
        ),
    )
    batch_ids = {c.id for c in claims}
    for action in result.get("actions", []):
        page_id = action.get("page_id") if action.get("kind") == "update" else None
        # LLM이 준 claim_ids 중 이번 배치에 실재하는 것만 인정. 비면(누락·환각) 문서의 클레임 전체로 폴백
        # — 페이지가 원본 소스와 끊기는 일이 없도록 보장한다.
        evidence = [cid for cid in action.get("claim_ids", []) if cid in batch_ids]
        if not evidence:
            log.warning("claim_ids 누락/무효 → 소스 클레임 전체 연결 폴백: %s", action.get("title"))
            evidence = sorted(batch_ids)
        apply_page_update(
            db,
            page_id=page_id,
            title=action.get("title", "제목 없음"),
            folder_path=action.get("folder_path", "공통"),
            page_type=action.get("page_type", "concept"),
            body=action.get("body", ""),
            cause="ingest",
            evidence_claim_ids=evidence,
        )


def apply_page_update(
    db: Session,
    *,
    page_id: int | None,
    title: str,
    folder_path: str,
    page_type: str,
    body: str,
    cause: str,
    evidence_claim_ids: list[int],
) -> WikiPage:
    """페이지 생성/갱신을 즉시 적용하고 버전 스냅샷과 클레임 링크를 남긴다."""
    from .projects import normalize_folder

    folder_path = normalize_folder(db, folder_path)
    title = _clean_title(title, folder_path)
    if page_id:
        page = db.get(WikiPage, page_id)
        if not page:
            raise ValueError(f"갱신 대상 페이지 없음: {page_id}")
        page.title, page.folder_path, page.body = title, folder_path, body
        page.page_type = page_type or page.page_type
        page.updated_at = datetime.utcnow()
    else:
        page = WikiPage(title=title, folder_path=folder_path,
                        page_type=page_type or "concept", body=body)
        db.add(page)
        db.flush()

    page.body = _with_related_section(db, page, _strip_related_section(body))
    db.add(WikiVersion(page_id=page.id, body=page.body, cause=cause))
    existing = {
        l.claim_id for l in db.scalars(select(ClaimWikiLink).where(ClaimWikiLink.page_id == page.id))
    }
    for cid in evidence_claim_ids or []:
        if cid not in existing and db.get(Claim, cid):
            db.add(ClaimWikiLink(claim_id=cid, page_id=page.id))
    db.commit()
    return page
