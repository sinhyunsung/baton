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
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import llm
from ..config import PARALLEL_WORKERS, SIM_ELEMENT, SIM_SUBJECT_OWNER
from ..db import SessionLocal
from ..models import Claim, ClaimWikiLink, LintIssue, LintRun, WikiPage
from .wiki import apply_page_update
from ..prompts.lint import GAP_SYSTEM, VERDICT_SYSTEM

log = logging.getLogger("baton.lint")

KIND_LABEL = {"owner": "담당자 충돌", "polarity": "참/거짓 충돌", "value": "값 충돌"}


def run_lint(db: Session, progress: dict | None = None) -> LintRun:
    """3단계 린트 — 위키 본문은 LLM에 넣지 않는다 (클레임이 검사 대상, 위키 교정은 답변 시점의 몫).

    A. 수집(벡터·룰, LLM 없음): 변경 페이지들의 충돌 후보 쌍 + 빈틈 검사용 클레임 목록
    B. 판정(전역 풀): 모든 판정·빈틈 LLM 호출을 PARALLEL_WORKERS(≤100)개 동시 실행
    C. 기록: 이슈 생성, 페이지 도장(그 페이지의 호출이 전부 성공했을 때만)
    """
    run = LintRun()
    db.add(run)
    db.commit()

    pages = db.scalars(
        select(WikiPage).where(
            or_(WikiPage.lint_checked_at.is_(None), WikiPage.updated_at > WikiPage.lint_checked_at)
        )
    ).all()

    prog = progress if progress is not None else {}
    started = _time.time()
    prog.update(pages_total=len(pages), pages_checked=0, llm_calls=0,
                contradictions=0, gaps=0, elapsed_sec=0, current="후보 수집 중(벡터)")

    # ── A. 후보 수집 (LLM 없음) ─────────────────────────────────────────────
    open_issues = db.scalars(select(LintIssue).where(LintIssue.status == "open")).all()
    open_pairs = {frozenset(i.claim_ids) for i in open_issues if i.claim_ids and len(i.claim_ids) == 2}
    open_gap_qs = {(i.page_id, i.question) for i in open_issues if i.issue_type == "gap"}

    verdict_tasks: list[dict] = []   # {page_id, a_id, b_id, a_view, b_view, hint, a_owner, b_owner, a_subject}
    gap_tasks: list[dict] = []       # {page_id, claims_text}
    seen_pairs: set[frozenset] = set(open_pairs)

    for page in pages:
        links = db.scalars(select(ClaimWikiLink).where(ClaimWikiLink.page_id == page.id)).all()
        page_claims = [db.get(Claim, l.claim_id) for l in links]
        page_claims = [c for c in page_claims if c]
        verdicts = 0
        for claim in page_claims:
            if claim.claim_type != "rule" or claim.superseded or claim.subject_vec is None:
                continue
            if verdicts >= MAX_VERDICTS_PER_PAGE:
                break
            rows = db.execute(
                select(Claim, Claim.subject_vec.cosine_distance(claim.subject_vec).label("d"))
                .where(Claim.id != claim.id, Claim.claim_type == "rule",
                       Claim.superseded.is_(False), Claim.source_id != claim.source_id,
                       Claim.subject_vec.isnot(None))
                .order_by("d").limit(15)
            ).all()
            for other, dist in rows:
                if verdicts >= MAX_VERDICTS_PER_PAGE:
                    break
                subj_sim = 1 - dist
                if subj_sim < 0.78:
                    break
                sn_a, sn_b = _numbers(claim.subject), _numbers(other.subject)
                if sn_a and sn_b and sn_a != sn_b:
                    continue
                pred_same = (_vcos(claim.predicate_vec, other.predicate_vec) >= SIM_ELEMENT
                             or (claim.predicate or "").strip() == (other.predicate or "").strip())
                if not pred_same:
                    continue
                hint = _element_conflict_hint(claim, other)
                if hint == "owner" and subj_sim < SIM_SUBJECT_OWNER:
                    hint = None
                pair = frozenset((claim.id, other.id))
                if hint is None or pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                verdicts += 1
                verdict_tasks.append({
                    "page_id": page.id, "a_id": claim.id, "b_id": other.id,
                    "a_view": _claim_view(claim), "b_view": _claim_view(other),
                    "hint": hint, "fallback_assignee": claim.owner or other.owner,
                    "a_subject": claim.subject,
                })
        # 빈틈: 페이지의 클레임 목록만 LLM에 (본문 아님)
        rules = [c for c in page_claims if c.claim_type == "rule" and not c.superseded]
        if rules:
            gap_tasks.append({
                "page_id": page.id,
                "claims_text": "\n".join(f"- {c.rendered_text}" for c in rules[:40]),
            })

    prog.update(current=f"LLM 판정 {len(verdict_tasks)}쌍 + 빈틈 {len(gap_tasks)}건 — 동시 {PARALLEL_WORKERS}")

    # ── B. 전역 LLM 풀 (동시 PARALLEL_WORKERS) ──────────────────────────────
    lock = threading.Lock()
    page_failed: set[int] = set()
    verdict_results: list[tuple[dict, dict]] = []
    gap_results: list[tuple[dict, dict]] = []

    def run_verdict(t: dict):
        v = llm.extract_json(VERDICT_SYSTEM, json.dumps(
            {"A": t["a_view"], "B": t["b_view"]}, ensure_ascii=False))
        return ("v", t, v)

    def run_gap(t: dict):
        g = llm.extract_json(GAP_SYSTEM, t["claims_text"])
        return ("g", t, g)

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
        futures = [pool.submit(run_verdict, t) for t in verdict_tasks]
        futures += [pool.submit(run_gap, t) for t in gap_tasks]
        for fut in as_completed(futures):
            with lock:
                run.llm_calls += 1
                prog.update(llm_calls=run.llm_calls, elapsed_sec=round(_time.time() - started))
            try:
                kind, t, out = fut.result()
            except Exception:  # noqa: BLE001 — 해당 페이지는 도장 안 찍고 다음 실행에서 재시도
                log.exception("린트 LLM 호출 실패")
                continue
            with lock:
                if kind == "v":
                    verdict_results.append((t, out))
                else:
                    gap_results.append((t, out))

    # 실패한 호출이 있던 페이지 추적 (성공 결과에 없는 태스크의 페이지)
    ok_tasks = {id(t) for t, _ in verdict_results} | {id(t) for t, _ in gap_results}
    for t in verdict_tasks + gap_tasks:
        if id(t) not in ok_tasks:
            page_failed.add(t["page_id"])

    # ── C. 기록 ─────────────────────────────────────────────────────────────
    for t, v in verdict_results:
        if v.get("verdict") != "conflict":
            continue
        kind = v.get("kind") or t["hint"]
        db.add(LintIssue(
            issue_type="contradiction", page_id=t["page_id"],
            claim_ids=[t["a_id"], t["b_id"]],
            question=v.get("question") or f"'{t['a_subject']}' 정보가 출처마다 다릅니다. 확인해주세요.",
            detail=f"[{KIND_LABEL.get(kind, kind)}] {v.get('reason', '')}",
            options=[o for o in (v.get("options") or []) if o][:4],
            assignee_name=v.get("assignee") or t["fallback_assignee"],
        ))
        run.contradictions += 1
    for t, g in gap_results:
        for gap in (g.get("gaps") or [])[:2]:
            if (t["page_id"], gap.get("question")) in open_gap_qs:
                continue
            db.add(LintIssue(
                issue_type="gap", page_id=t["page_id"], claim_ids=[],
                question=gap.get("question", "확인이 필요합니다"),
                detail=gap.get("detail"), options=[], assignee_name=gap.get("assignee"),
            ))
            run.gaps += 1
    for page in pages:
        if page.id not in page_failed:
            page.lint_checked_at = datetime.utcnow()
        run.pages_checked += 1
    run.finished_at = datetime.utcnow()
    db.commit()
    prog.update(pages_checked=run.pages_checked, contradictions=run.contradictions,
                gaps=run.gaps, done=True, current=None,
                elapsed_sec=round(_time.time() - started))
    return run


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


def _claim_view(c: Claim) -> dict:
    return {"id": c.id, "text": c.rendered_text, "subject": c.subject, "predicate": c.predicate,
            "object": c.object, "polarity": c.polarity, "owner": c.owner, "project": c.project,
            "scope": c.scope, "time_scope": c.time_scope,
            "confidence": c.confidence, "human_confirmed": c.human_confirmed}


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
