"""배치 린트: 변경된 위키만 선별 → 위키 클레임 즉석 추출 → 룰 필터 → 애매한 쌍만 LLM 판정."""
import json
import logging
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import llm
from ..config import SIM_LINT_PAIR
from ..models import Claim, LintIssue, LintRun, WikiPage, WikiProposal, WikiVersion
from ..prompts.lint import GAP_SYSTEM, VERDICT_SYSTEM, WIKI_CLAIMS_SYSTEM

log = logging.getLogger("baton.lint")


def run_lint(db: Session) -> LintRun:
    run = LintRun()
    db.add(run)
    db.commit()

    pages = db.scalars(
        select(WikiPage).where(
            or_(WikiPage.lint_checked_at.is_(None), WikiPage.updated_at > WikiPage.lint_checked_at)
        )
    ).all()

    for page in pages:
        try:
            _lint_page(db, page, run)
        except Exception:  # noqa: BLE001
            log.exception("린트 실패: page=%s", page.id)
            db.rollback()
        page.lint_checked_at = datetime.utcnow()
        run.pages_checked += 1
        db.commit()

    run.finished_at = datetime.utcnow()
    db.commit()
    return run


def _lint_page(db: Session, page: WikiPage, run: LintRun):
    # 1) 위키 클레임 즉석 추출 (저장 안 함)
    result = llm.extract_json(WIKI_CLAIMS_SYSTEM, f"# {page.title}\n\n{page.body[:8000]}")
    run.llm_calls += 1
    wiki_claims = result.get("claims", [])

    # 2) 각 위키 클레임과 유사한 소스 클레임 검색(rule만) → 룰 필터 → 애매한 쌍만 LLM
    for wc in wiki_claims:
        text = f"{wc.get('subject','')}의 {wc.get('predicate','')}은(는) {wc.get('object','')}"
        vec = llm.embed([text], input_type="search_query")[0]
        rows = db.execute(
            select(Claim, Claim.embedding.cosine_distance(vec).label("d"))
            .where(Claim.claim_type == "rule", Claim.superseded.is_(False))
            .order_by("d").limit(4)
        ).all()
        for claim, dist in rows:
            if 1 - dist < SIM_LINT_PAIR:
                continue
            # 룰 필터: 값이 같으면 통과, 이미 열린 이슈가 있으면 스킵
            if str(wc.get("object", "")).strip() == claim.object.strip():
                continue
            if db.scalar(select(LintIssue).where(
                LintIssue.page_id == page.id, LintIssue.status == "open",
                LintIssue.claim_ids.contains([claim.id]))):
                continue
            verdict = llm.extract_json(VERDICT_SYSTEM, json.dumps({
                "위키_클레임": wc,
                "소스_클레임": {"id": claim.id, "text": claim.rendered_text,
                               "confidence": claim.confidence,
                               "human_confirmed": claim.human_confirmed},
            }, ensure_ascii=False))
            run.llm_calls += 1
            if verdict.get("verdict") == "contradiction":
                db.add(LintIssue(
                    issue_type="contradiction", page_id=page.id, claim_ids=[claim.id],
                    question=verdict.get("question") or f"'{page.title}'의 값이 출처마다 다릅니다. 확인해주세요.",
                    detail=verdict.get("reason"),
                    options=[o for o in verdict.get("options", []) if o][:4],
                    assignee_name=verdict.get("assignee"),
                ))
                run.contradictions += 1
                db.commit()

    # 3) 빈틈 검사 (페이지당 1회)
    gaps = llm.extract_json(GAP_SYSTEM, f"# {page.title}\n\n{page.body[:8000]}")
    run.llm_calls += 1
    for g in gaps.get("gaps", [])[:2]:
        if db.scalar(select(LintIssue).where(
            LintIssue.page_id == page.id, LintIssue.status == "open",
            LintIssue.issue_type == "gap", LintIssue.question == g.get("question"))):
            continue
        db.add(LintIssue(
            issue_type="gap", page_id=page.id, claim_ids=[],
            question=g.get("question", "확인이 필요합니다"),
            detail=g.get("detail"), options=[], assignee_name=g.get("assignee"),
        ))
        run.gaps += 1
    db.commit()


def answer_issue(db: Session, issue_id: int, answer: str) -> LintIssue:
    """역질문 답변 처리: 확정 값과 일치하는 소스 클레임에 human_confirmed, 나머지는 superseded.
    위키 반영은 갱신안(proposal) 생성으로."""
    issue = db.get(LintIssue, issue_id)
    if not issue or issue.status != "open":
        raise ValueError("열린 확인 요청이 아님")

    confirmed_claim = None
    for cid in issue.claim_ids or []:
        claim = db.get(Claim, cid)
        if not claim:
            continue
        if answer.strip() in claim.object or claim.object in answer.strip():
            claim.human_confirmed = True
            confirmed_claim = claim
        else:
            claim.superseded = True

    issue.status = "answered"
    issue.answer = answer
    issue.resolved_at = datetime.utcnow()
    db.commit()

    # 위키 갱신안 생성 (사람이 검토 후 반영)
    if issue.page_id:
        page = db.get(WikiPage, issue.page_id)
        if page:
            new_body = llm.complete(
                "너는 위키 편집자다. 아래 위키 본문을, 확인된 답변을 반영해 전체 마크다운으로 다시 써라. "
                "확인된 값에는 '(확인됨)' 표기를 붙인다. 본문만 출력.",
                f"# {page.title}\n\n{page.body}\n\n## 확인된 답변\n질문: {issue.question}\n답변: {answer}",
            )
            db.add(WikiProposal(
                page_id=page.id, new_title=page.title, new_folder_path=page.folder_path,
                new_body=new_body.strip(), cause="answer",
                evidence_claim_ids=[confirmed_claim.id] if confirmed_claim else [],
            ))
            db.commit()
    return issue
