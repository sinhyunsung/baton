"""위키 갱신 적용. LLM이 결정한 갱신(create/update)을 즉시 위키에 반영한다 (버전 스냅샷 보존)."""
import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..config import SIM_WIKI_MATCH
from ..models import Claim, ClaimWikiLink, RawSource, WikiPage, WikiVersion
from ..prompts.wiki import WIKI_DECIDE_SYSTEM, WIKI_DECIDE_USER

log = logging.getLogger("baton.wiki")


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

    result = llm.extract_json(
        WIKI_DECIDE_SYSTEM,
        WIKI_DECIDE_USER.format(
            source_desc=f"{src.source_type}/{src.path} — {src.title}",
            claims_json=claims_json, candidates_json=candidates_json,
        ),
    )
    for action in result.get("actions", []):
        page_id = action.get("page_id") if action.get("kind") == "update" else None
        apply_page_update(
            db,
            page_id=page_id,
            title=action.get("title", "제목 없음"),
            folder_path=action.get("folder_path", "공통"),
            page_type=action.get("page_type", "concept"),
            body=action.get("body", ""),
            cause="ingest",
            evidence_claim_ids=[cid for cid in action.get("claim_ids", []) if cid],
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

    db.add(WikiVersion(page_id=page.id, body=body, cause=cause))
    existing = {
        l.claim_id for l in db.scalars(select(ClaimWikiLink).where(ClaimWikiLink.page_id == page.id))
    }
    for cid in evidence_claim_ids or []:
        if cid not in existing and db.get(Claim, cid):
            db.add(ClaimWikiLink(claim_id=cid, page_id=page.id))
    db.commit()
    return page
