"""시맨틱 청킹 — 의미가 급변하는(주제가 환기되는) 지점을 경계로 본문을 조각낸다.

방법:
1) 본문을 단위(unit)로 1차 분해: 마크다운 헤더는 강제 경계, 그 외 빈 줄 기준 문단.
   너무 짧은 단위(<80자)는 이웃과 합쳐 임베딩 노이즈를 줄인다.
2) 단위들을 임베딩하고, "지금 쌓고 있는 청크의 평균 벡터 vs 다음 단위" 코사인을 계산.
3) 유사도가 문턱(SEM_CHUNK_SIM) 아래로 떨어지면 = 주제 전환 → 청크 경계.
   최대 길이(SEM_CHUNK_MAX)를 넘으면 의미와 무관하게 강제 경계(LLM 입력 가드).
"""
import logging
import math
import re

from .. import llm
from ..config import SEM_CHUNK_MAX, SEM_CHUNK_MIN, SEM_CHUNK_SIM

log = logging.getLogger("baton.chunking")

_HEADER_RE = re.compile(r"^#{1,6}\s", re.M)
_TOP_HEADER_RE = re.compile(r"^#{1,2}\s")  # 강제 경계는 상위 헤더(#, ##)만 — ###부턴 의미 판정에 맡긴다


def _cos(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def _mean(vecs: list[list]) -> list:
    return [sum(col) / len(vecs) for col in zip(*vecs)]


def _units(body: str) -> list[tuple[str, bool]]:
    """(텍스트, 헤더로 시작하는가) 단위 목록. 짧은 단위는 이웃과 병합."""
    parts: list[tuple[str, bool]] = []
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block:
            continue
        is_header = bool(_HEADER_RE.match(block))
        # 헤더와 바로 다음 문단이 한 블록이면 그대로 (헤더가 주제 신호)
        if parts and not is_header and len(parts[-1][0]) < 80:
            parts[-1] = (parts[-1][0] + "\n" + block, parts[-1][1])
        else:
            parts.append((block, is_header))
    # 여전히 80자 미만인 꼬리 단위는 앞에 병합
    merged: list[tuple[str, bool]] = []
    for text, hdr in parts:
        if merged and len(text) < 80 and not hdr:
            merged[-1] = (merged[-1][0] + "\n" + text, merged[-1][1])
        else:
            merged.append((text, hdr))
    return merged


def semantic_chunks(body: str) -> list[str]:
    """의미 경계 기반 청크 목록. 짧은 문서는 통째로 1청크."""
    body = (body or "").strip()
    if len(body) <= SEM_CHUNK_MIN * 2:
        return [body] if body else []
    units = _units(body)
    if len(units) <= 2:
        return [body]

    try:
        vecs = llm.embed([u[0][:2000] for u in units], input_type="search_document")
    except Exception:  # noqa: BLE001 — 임베딩 실패 시 청킹 없이 통째 (비치명)
        log.exception("청킹 임베딩 실패 — 본문 통째 사용")
        return [body]

    chunks: list[str] = []
    cur_texts: list[str] = []
    cur_vecs: list[list] = []
    cur_len = 0

    def flush():
        nonlocal cur_texts, cur_vecs, cur_len
        if cur_texts:
            chunks.append("\n\n".join(cur_texts))
        cur_texts, cur_vecs, cur_len = [], [], 0

    for (text, is_header), vec in zip(units, vecs):
        if cur_texts:
            sim = _cos(_mean(cur_vecs), vec)
            topic_shift = sim < SEM_CHUNK_SIM        # 의미 급변 = 주제 환기 지점
            header_break = bool(_TOP_HEADER_RE.match(text)) and cur_len >= SEM_CHUNK_MIN  # 상위 헤더만 자연 경계
            too_long = cur_len + len(text) > SEM_CHUNK_MAX
            if (topic_shift and cur_len >= SEM_CHUNK_MIN) or header_break or too_long:
                flush()
        cur_texts.append(text)
        cur_vecs.append(vec)
        cur_len += len(text)
    flush()

    log.info("시맨틱 청킹: %d자 → %d청크 (%s)", len(body), len(chunks),
             ", ".join(str(len(c)) for c in chunks))
    return chunks
