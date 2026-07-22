"""검색·챗봇: 질의 → 클레임 변환 → 벡터 유사도 → 원본/위키 역추적 → 답변 생성(근거 0건이면 역질문 제안)."""
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..config import SIM_ANSWER_MIN
from ..models import Claim, ClaimWikiLink, LintIssue, RawSource, WikiPage
from ..prompts.chat import ANSWER_SYSTEM, ANSWER_USER, QUERY_PARSE_SYSTEM

log = logging.getLogger("baton.chat")


def retrieve(db: Session, question: str, k: int = 8) -> dict:
    """역추적 검색. 반환: {parsed, matches:[{claim, sim, source, pages}], pages}"""
    parsed = llm.extract_json(QUERY_PARSE_SYSTEM, question)
    search_text = parsed.get("search_text") or question
    vec = llm.embed([search_text], input_type="search_query")[0]

    rows = db.execute(
        select(Claim, Claim.embedding.cosine_distance(vec).label("d"))
        .where(Claim.superseded.is_(False))
        .order_by("d").limit(k)
    ).all()

    matches, page_map = [], {}
    for claim, dist in rows:
        sim = 1 - dist
        if sim < SIM_ANSWER_MIN:
            continue
        src = db.get(RawSource, claim.source_id)
        links = db.scalars(select(ClaimWikiLink).where(ClaimWikiLink.claim_id == claim.id)).all()
        pages = [db.get(WikiPage, l.page_id) for l in links]
        pages = [p for p in pages if p]
        for p in pages:
            page_map[p.id] = p
        matches.append({"claim": claim, "sim": round(sim, 3), "source": src, "pages": pages})
    return {"parsed": parsed, "search_text": search_text, "matches": matches,
            "pages": list(page_map.values())}


def build_contexts(r: dict) -> tuple[str, str]:
    wiki_ctx = "\n\n".join(
        f"### [{p.id}] {p.title} ({p.folder_path})\n{p.body[:4000]}" for p in r["pages"]
    ) or "(연결된 위키 페이지 없음)"
    claims_ctx = "\n".join(
        f"- [{m['claim'].id}] {m['claim'].rendered_text} "
        f"(확신도 {m['claim'].confidence}, {'확인됨' if m['claim'].human_confirmed else '미확인'}, "
        f"유사도 {m['sim']}, 출처: {m['source'].source_type}/{m['source'].title})"
        for m in r["matches"]
    ) or "(근거 클레임 없음)"
    return wiki_ctx, claims_ctx


def answer_stream(db: Session, question: str):
    """SSE용 제너레이터. 이벤트: meta(근거) → token* → done | no_evidence"""
    r = retrieve(db, question)
    if not r["matches"]:
        yield {"event": "no_evidence", "data": json.dumps({
            "message": "위키에 확정된 지식이 없습니다(근거 0건). 역질문을 보낼까요?",
            "search_text": r["search_text"],
        }, ensure_ascii=False)}
        return

    meta = {
        "sources": [{
            "claim_id": m["claim"].id, "claim_text": m["claim"].rendered_text,
            "sim": m["sim"], "human_confirmed": m["claim"].human_confirmed,
            "source_id": m["source"].id, "source_type": m["source"].source_type,
            "source_title": m["source"].title,
            "pages": [{"id": p.id, "title": p.title, "folder_path": p.folder_path} for p in m["pages"]],
        } for m in r["matches"]],
    }
    yield {"event": "meta", "data": json.dumps(meta, ensure_ascii=False)}

    wiki_ctx, claims_ctx = build_contexts(r)
    for token in llm.stream(
        ANSWER_SYSTEM,
        ANSWER_USER.format(question=question, wiki_context=wiki_ctx, claims_context=claims_ctx),
    ):
        yield {"event": "token", "data": token}
    yield {"event": "done", "data": "{}"}


def create_backquestion(db: Session, question: str) -> LintIssue:
    """근거 0건 질문 → 확인 요청함에 역질문 게시."""
    issue = LintIssue(
        issue_type="gap", page_id=None, claim_ids=[],
        question=f"'{question}'에 대한 확정 지식이 없습니다. 아는 분은 답변해주세요.",
        detail="챗봇 질의에서 근거 소스 0건으로 생성된 역질문", options=[],
    )
    db.add(issue)
    db.commit()
    return issue
