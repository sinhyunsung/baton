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
            "updated_at": p.updated_at.isoformat(),
            "claims": n_claims, "confirmed": n_confirmed, "lint_open": n_lint,
        })
    return {"pages": out}


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
            "confidence": c.confidence, "human_confirmed": c.human_confirmed,
            "superseded": c.superseded,
            "source": {"id": src.id, "type": src.source_type, "title": src.title, "path": src.path},
        })
    issues = db.scalars(
        select(LintIssue).where(LintIssue.page_id == page_id, LintIssue.status == "open")
    ).all()
    return {
        "id": p.id, "title": p.title, "folder_path": p.folder_path, "body": p.body,
        "updated_at": p.updated_at.isoformat(), "claims": claims,
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
