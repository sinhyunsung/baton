"""위키 갱신안 생성·승인. LLM은 위키를 직접 수정하지 않고 갱신안(proposal)만 만든다."""
import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..config import SIM_WIKI_MATCH
from ..models import Claim, ClaimWikiLink, RawSource, WikiPage, WikiProposal, WikiVersion
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


def propose_wiki_updates(db: Session, src: RawSource, claims: list[Claim], auto_approve: bool = False):
    candidates = find_candidate_pages(db, claims)
    claims_json = json.dumps(
        [{"id": c.id, "text": c.rendered_text, "claim_type": c.claim_type,
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
        prop = WikiProposal(
            page_id=page_id,
            new_title=action.get("title", "제목 없음"),
            new_folder_path=action.get("folder_path", "공통"),
            new_page_type=action.get("page_type", "concept"),
            new_body=action.get("body", ""),
            cause="ingest",
            evidence_claim_ids=[cid for cid in action.get("claim_ids", []) if cid],
        )
        db.add(prop)
        db.commit()
        if auto_approve:
            approve_proposal(db, prop.id)


def approve_proposal(db: Session, proposal_id: int, edited_body: str | None = None) -> WikiPage:
    prop = db.get(WikiProposal, proposal_id)
    if not prop or prop.status != "pending":
        raise ValueError("대기 중인 갱신안이 아님")
    body = edited_body if edited_body is not None else prop.new_body

    if prop.page_id:
        page = db.get(WikiPage, prop.page_id)
        page.title, page.folder_path, page.body = prop.new_title, prop.new_folder_path, body
        page.page_type = prop.new_page_type or page.page_type
        page.updated_at = datetime.utcnow()
    else:
        page = WikiPage(title=prop.new_title, folder_path=prop.new_folder_path,
                        page_type=prop.new_page_type or "concept", body=body)
        db.add(page)
        db.flush()

    db.add(WikiVersion(page_id=page.id, body=body, cause=prop.cause))
    existing = {
        l.claim_id for l in db.scalars(select(ClaimWikiLink).where(ClaimWikiLink.page_id == page.id))
    }
    for cid in prop.evidence_claim_ids or []:
        if cid not in existing and db.get(Claim, cid):
            db.add(ClaimWikiLink(claim_id=cid, page_id=page.id))
    prop.status = "approved"
    prop.resolved_at = datetime.utcnow()
    db.commit()
    return page


def reject_proposal(db: Session, proposal_id: int):
    prop = db.get(WikiProposal, proposal_id)
    if not prop or prop.status != "pending":
        raise ValueError("대기 중인 갱신안이 아님")
    prop.status = "rejected"
    prop.resolved_at = datetime.utcnow()
    db.commit()
