"""배치 린트: 변경된 위키만 선별 → 요소 매칭으로 "같은 정보" 쌍 탐색 → 판별 요소만 다르면 충돌 후보 → LLM 최종 판정.

요소 매칭: 주어·속성·값을 각각 임베딩해 두고,
  주어 유사 ≥ 0.70 AND 속성 유사 ≥ 0.70  →  "같은 정보를 말하는 쌍"으로 확정.
그 위에서 판별 요소만 검사한다:
- owner   : 담당자가 서로 다름 → 담당자 충돌
- polarity: 값은 같은 의미인데 긍/부정 반대 → 참/거짓 충돌
- value   : 값의 의미가 다름 → 값 충돌
scope·time_scope가 서로 다르면 양립 가능(이력·범위 차이)으로 보고 충돌에서 제외한다.
"""
import json
import logging
import re
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import llm
from ..config import SIM_ELEMENT, SIM_SUBJECT_OWNER
from ..models import Claim, ClaimWikiLink, LintIssue, LintRun, WikiPage
from .wiki import apply_page_update
from ..prompts.lint import GAP_SYSTEM, VERDICT_SYSTEM

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
            _lint_page_gaps(db, page, run)     # ② 빈틈 (담당자·기한 미지정 우선)
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


def _vcos(a, b) -> float:
    if a is None or b is None:
        return 0.0
    from .ingest import _cos
    return _cos(list(a), list(b))


def _aux_compatible(a: str | None, b: str | None) -> bool:
    """scope·time_scope 호환성: 한쪽이라도 없으면 호환, 둘 다 있으면 같은 의미일 때만.
    서로 다른 범위·시점이면 두 값이 양립 가능(이력)하므로 충돌이 아니다."""
    if not a or not b:
        return True
    return a.strip() == b.strip() or _values_equivalent(a, b)


_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> tuple:
    return tuple(sorted(n.replace(",", "") for n in _NUM_RE.findall(str(text or ""))))


def _element_conflict_hint(a: Claim, b: Claim) -> str | None:
    """요소 매칭 룰 필터: 주어·속성이 같은 쌍에서 판별 요소만 다를 때 충돌 후보.
    (주어·속성 유사는 호출부에서 이미 확인된 상태로 들어온다)"""
    # 범위·시점이 다르면 양립 가능 — 모순이 아니라 이력/한정 차이
    if not _aux_compatible(a.scope, b.scope) or not _aux_compatible(a.time_scope, b.time_scope):
        return None
    same_obj = (_vcos(a.object_vec, b.object_vec) >= SIM_ELEMENT
                or _values_equivalent(a.object, b.object, context=_claims_context(a, b)))
    # 숫자 가드: 임베딩은 수량을 구분 못 한다 ("30분"≈"2시간"이 0.7을 넘음).
    # 양쪽 값에 숫자가 있는데 숫자가 다르면 무조건 다른 값으로 본다.
    na, nb = _numbers(a.object), _numbers(b.object)
    if na and nb and na != nb:
        same_obj = False
    # 담당자 충돌: 같은 정보인데 담당자가 서로 다름
    if a.owner and b.owner and a.owner.strip() != b.owner.strip():
        return "owner"
    # 참/거짓 충돌: 값은 같은 의미인데 긍/부정이 반대
    if same_obj and a.polarity != b.polarity:
        return "polarity"
    # 값 충돌: 값의 의미가 다름 (극성까지 같을 때 — 극성 다르고 값도 다르면 다른 얘기일 확률)
    if not same_obj and a.polarity == b.polarity:
        return "value"
    return None


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
        if claim.subject_vec is None:
            continue
        # 주어가 같은(≥0.70) 클레임을 넓게 모은 뒤, 속성까지 같아야 "같은 정보"로 본다
        rows = db.execute(
            select(Claim, Claim.subject_vec.cosine_distance(claim.subject_vec).label("d"))
            .where(Claim.id != claim.id, Claim.claim_type == "rule",
                   Claim.superseded.is_(False), Claim.source_id != claim.source_id,
                   Claim.subject_vec.isnot(None))
            .order_by("d").limit(15)
        ).all()
        for other, dist in rows:
            subj_sim = 1 - dist
            if subj_sim < 0.78:
                break  # 거리순 정렬이므로 이후는 전부 문턱 미달
            pred_same = (_vcos(claim.predicate_vec, other.predicate_vec) >= SIM_ELEMENT
                         or (claim.predicate or "").strip() == (other.predicate or "").strip())
            if not pred_same:
                continue  # 같은 대상의 다른 속성 얘기 (기한 vs 담당자) — 충돌 아님
            hint = _element_conflict_hint(claim, other)
            # 담당자 충돌은 "정말 같은 대상"일 때만 — 서식이 비슷한 서로 다른 건(계약 A사/B사)의
            # 담당자가 다른 건 당연하므로, 주어 유사가 아주 높을 때만 인정한다
            if hint == "owner" and subj_sim < SIM_SUBJECT_OWNER:
                hint = None
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

    if issue.issue_type == "taxonomy":
        from .taxonomy import answer_taxonomy
        answer_taxonomy(db, issue, answer)
        issue.status = "answered"
        issue.answer = answer
        issue.resolved_at = datetime.utcnow()
        db.commit()
        return issue

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
