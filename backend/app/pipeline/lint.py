"""배치 린트: 변경된 위키만 선별 → 클레임 충돌 감지(담당자/참·거짓/값) → 룰 우선, 애매한 쌍만 LLM 판정.

충돌 3종 (사용자가 확인 요청함에서 선택지로 고른다):
- owner   : 같은 정보인데 담당자가 다름
- polarity: 같은 정보에 대해 참/거짓이 갈림
- value   : 같은 대상·속성인데 값이 다름
"""
import json
import logging
import re
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import llm
from ..config import SIM_LINT_PAIR
from ..models import Claim, ClaimWikiLink, LintIssue, LintRun, WikiPage
from .wiki import apply_page_update
from ..prompts.lint import GAP_SYSTEM, VERDICT_SYSTEM, WIKI_CLAIMS_SYSTEM

log = logging.getLogger("baton.lint")

KIND_LABEL = {"owner": "담당자 충돌", "polarity": "참/거짓 충돌", "value": "값 충돌"}


def run_lint(db: Session, progress: dict | None = None) -> LintRun:
    run = LintRun()
    db.add(run)
    db.commit()

    pages = db.scalars(
        select(WikiPage).where(
            or_(WikiPage.lint_checked_at.is_(None), WikiPage.updated_at > WikiPage.lint_checked_at)
        )
    ).all()

    import time as _time
    prog = progress if progress is not None else {}
    started = _time.time()
    prog.update(pages_total=len(pages), pages_checked=0, llm_calls=0,
                contradictions=0, gaps=0, elapsed_sec=0, current=None)

    for page in pages:
        prog["current"] = page.title
        ok = True
        try:
            _lint_page_claims(db, page, run)   # ① 소스 클레임끼리 충돌 (담당자/참거짓/값)
            _lint_page_wiki(db, page, run)     # ② 위키 본문 vs 소스 클레임 표류 검사
            _lint_page_gaps(db, page, run)     # ③ 빈틈 (담당자·기한 미지정 우선)
        except Exception:  # noqa: BLE001
            log.exception("린트 실패: page=%s", page.id)
            db.rollback()
            ok = False
        if ok:  # 실패한 페이지는 미검사로 남겨 다음 실행에서 다시 본다
            page.lint_checked_at = datetime.utcnow()
        run.pages_checked += 1
        db.commit()
        prog.update(pages_checked=run.pages_checked, llm_calls=run.llm_calls,
                    contradictions=run.contradictions, gaps=run.gaps,
                    elapsed_sec=round(_time.time() - started), current=None)

    run.finished_at = datetime.utcnow()
    db.commit()
    prog.update(done=True, elapsed_sec=round(_time.time() - started))
    return run


def _pair_has_open_issue(db: Session, a_id: int, b_id: int) -> bool:
    issues = db.scalars(select(LintIssue).where(LintIssue.status == "open")).all()
    return any(a_id in (i.claim_ids or []) and b_id in (i.claim_ids or []) for i in issues)


_STOPWORDS = {"및", "등", "관련", "그리고", "또한", "최종", "추가", "반드시", "즉시"}


def _norm_tokens(text) -> set[str]:
    toks = re.split(r"[\s,·/()\[\]\"']+", str(text or "").strip())
    return {t for t in toks if t and t not in _STOPWORDS}


def _values_equivalent(a, b, context: str = "") -> bool:
    """표현만 다르고 의미가 같은 값인지 (엄격: 수식어·문맥 중복 제거 후 한쪽이 다른 쪽에 완전히 포함).
    예: 'COO 최종 승인 필요' ⊂ '재무담당 및 COO 추가 승인 필요' → 같은 의미로 본다.
    context: 두 클레임의 subject/scope 등 — 값에 섞여 든 조건이 상대 클레임에선 주어에 있을 수 있어,
    문맥에 이미 있는 토큰은 비교에서 제외한다 ('50만원 초과 시 COO 승인' vs subject='50만원 초과 비용')."""
    ctx = _norm_tokens(context)
    A = {t for t in _norm_tokens(a) if t not in ctx and len(t) >= 2}
    B = {t for t in _norm_tokens(b) if t not in ctx and len(t) >= 2}
    if not A or not B:
        return False
    small, big = (A, B) if len(A) <= len(B) else (B, A)
    return small <= big


def _claims_context(a: Claim, b: Claim) -> str:
    return " ".join(filter(None, [a.subject, b.subject, a.scope, b.scope, a.time_scope, b.time_scope]))


def _rule_conflict_hint(a: Claim, b: Claim) -> str | None:
    """룰 필터: LLM 없이 충돌 후보 종류를 추정. None이면 후보 아님."""
    # 담당자 충돌: 같은 얘기(유사도는 호출부에서 확인)인데 담당자가 서로 다름
    if a.owner and b.owner and a.owner.strip() != b.owner.strip():
        return "owner"
    same_meaning = _values_equivalent(a.object, b.object, context=_claims_context(a, b))
    # 참/거짓 충돌: 같은 의미의 값을 두고 긍정/부정이 갈림
    if same_meaning and a.polarity != b.polarity:
        return "polarity"
    # 같은 의미(포함 관계)면 충돌이 아니다 — 표현 차이만 있는 쌍은 여기서 걸러 LLM도 안 태운다
    if same_meaning:
        return None
    # 값 충돌: 의미가 다른 값
    return "value"


MAX_VERDICTS_PER_PAGE = 12  # 페이지당 LLM 판정 상한 (표 문서 등에서 쌍 폭주 방지)


def _lint_page_claims(db: Session, page: WikiPage, run: LintRun):
    """페이지에 연결된 소스 클레임 각각에 대해, 유사한 다른 소스 클레임과 충돌하는지 검사."""
    links = db.scalars(select(ClaimWikiLink).where(ClaimWikiLink.page_id == page.id)).all()
    verdicts = 0
    open_issues = db.scalars(select(LintIssue).where(LintIssue.status == "open")).all()
    open_pairs = {frozenset(i.claim_ids) for i in open_issues if i.claim_ids and len(i.claim_ids) == 2}
    for link in links:
        if verdicts >= MAX_VERDICTS_PER_PAGE:
            break
        claim = db.get(Claim, link.claim_id)
        if not claim or claim.claim_type != "rule" or claim.superseded:
            continue
        rows = db.execute(
            select(Claim, Claim.embedding.cosine_distance(claim.embedding).label("d"))
            .where(Claim.id != claim.id, Claim.claim_type == "rule",
                   Claim.superseded.is_(False), Claim.source_id != claim.source_id)
            .order_by("d").limit(4)
        ).all()
        for other, dist in rows:
            if 1 - dist < SIM_LINT_PAIR:
                continue
            hint = _rule_conflict_hint(claim, other)
            if hint is None or frozenset((claim.id, other.id)) in open_pairs:
                continue
            verdict = llm.extract_json(VERDICT_SYSTEM, json.dumps({
                "A": _claim_view(claim), "B": _claim_view(other),
            }, ensure_ascii=False))
            run.llm_calls += 1
            verdicts += 1
            if verdict.get("verdict") != "conflict":
                continue
            kind = verdict.get("kind") or hint
            db.add(LintIssue(
                issue_type="contradiction", page_id=page.id,
                claim_ids=[claim.id, other.id],
                question=verdict.get("question") or f"'{claim.subject}' 정보가 출처마다 다릅니다. 확인해주세요.",
                detail=f"[{KIND_LABEL.get(kind, kind)}] {verdict.get('reason', '')}",
                options=[o for o in verdict.get("options", []) if o][:4],
                assignee_name=verdict.get("assignee") or claim.owner or other.owner,
            ))
            run.contradictions += 1
            db.commit()


def _claim_view(c: Claim) -> dict:
    return {"id": c.id, "text": c.rendered_text, "subject": c.subject, "predicate": c.predicate,
            "object": c.object, "polarity": c.polarity, "owner": c.owner, "project": c.project,
            "scope": c.scope, "time_scope": c.time_scope,
            "confidence": c.confidence, "human_confirmed": c.human_confirmed}


def _lint_page_wiki(db: Session, page: WikiPage, run: LintRun):
    """위키 본문에서 클레임 즉석 추출(저장 안 함) → 소스 클레임과 대조 (본문 표류 감지)."""
    result = llm.extract_json(WIKI_CLAIMS_SYSTEM, f"# {page.title}\n\n{page.body[:8000]}")
    run.llm_calls += 1
    for wc in result.get("claims", []):
        text = f"{wc.get('subject','')}의 {wc.get('predicate','')}은(는) {wc.get('object','')}"
        vec = llm.embed([text], input_type="search_query")[0]
        rows = db.execute(
            select(Claim, Claim.embedding.cosine_distance(vec).label("d"))
            .where(Claim.claim_type == "rule", Claim.superseded.is_(False))
            .order_by("d").limit(3)
        ).all()
        for claim, dist in rows:
            if 1 - dist < SIM_LINT_PAIR:
                continue
            same_value = _values_equivalent(wc.get("object", ""), claim.object, context=" ".join(filter(None, [wc.get("subject"), claim.subject, claim.scope, claim.time_scope])))
            same_owner = not wc.get("owner") or not claim.owner or wc["owner"].strip() == claim.owner.strip()
            if same_value and same_owner:
                continue
            if db.scalar(select(LintIssue).where(
                LintIssue.page_id == page.id, LintIssue.status == "open",
                LintIssue.claim_ids.contains([claim.id]))):
                continue
            verdict = llm.extract_json(VERDICT_SYSTEM, json.dumps({
                "A": {"출처": "위키 본문", **wc}, "B": _claim_view(claim),
            }, ensure_ascii=False))
            run.llm_calls += 1
            if verdict.get("verdict") != "conflict":
                continue
            kind = verdict.get("kind") or "value"
            db.add(LintIssue(
                issue_type="contradiction", page_id=page.id, claim_ids=[claim.id],
                question=verdict.get("question") or f"'{page.title}'의 값이 출처마다 다릅니다. 확인해주세요.",
                detail=f"[{KIND_LABEL.get(kind, kind)} · 위키↔소스] {verdict.get('reason', '')}",
                options=[o for o in verdict.get("options", []) if o][:4],
                assignee_name=verdict.get("assignee") or claim.owner,
            ))
            run.contradictions += 1
            db.commit()


def _lint_page_gaps(db: Session, page: WikiPage, run: LintRun):
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
    """역질문 답변: 선택된 값/담당자와 일치하는 클레임에 human_confirmed, 나머지는 superseded.
    위키 반영은 갱신안(proposal) 생성으로."""
    issue = db.get(LintIssue, issue_id)
    if not issue or issue.status != "open":
        raise ValueError("열린 확인 요청이 아님")

    ans = answer.strip()
    confirmed_claim = None
    for cid in issue.claim_ids or []:
        claim = db.get(Claim, cid)
        if not claim:
            continue
        # 값(object) 또는 담당자(owner)와 매칭 — 담당자 충돌 답변도 처리
        cands = [claim.object] + ([claim.owner] if claim.owner else [])
        matched = any(ans in c or c in ans for c in cands if c)
        if matched:
            claim.human_confirmed = True
            confirmed_claim = claim
        else:
            claim.superseded = True

    issue.status = "answered"
    issue.answer = answer
    issue.resolved_at = datetime.utcnow()
    db.commit()

    # 확인된 답변을 위키에 즉시 반영
    if issue.page_id:
        page = db.get(WikiPage, issue.page_id)
        if page:
            new_body = llm.complete(
                "너는 위키 편집자다. 아래 위키 본문을, 확인된 답변을 반영해 전체 마크다운으로 다시 써라. "
                "확인된 값에는 '(확인됨)' 표기를 붙이고, 밀려난 값은 '반론과 불확실성'에서 제거하거나 이력으로 남긴다. 본문만 출력.",
                f"# {page.title}\n\n{page.body}\n\n## 확인된 답변\n질문: {issue.question}\n답변: {answer}",
            )
            apply_page_update(
                db, page_id=page.id, title=page.title, folder_path=page.folder_path,
                page_type=page.page_type, body=new_body.strip(), cause="answer",
                evidence_claim_ids=[confirmed_claim.id] if confirmed_claim else [],
            )
    return issue
