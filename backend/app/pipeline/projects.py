"""프로젝트명 정규화 — n-gram 빈도 기반 통합.

문제: LLM이 문서마다 "두레로지스", "두레로지스 웹훅", "두레로지스 캐싱 전략"처럼
프로젝트명을 조금씩 다르게 뽑아 트리의 프로젝트 계층이 무한히 파편화된다.

전략: 지금까지 등장한 모든 프로젝트명을 공백 토큰으로 쪼개 연속 n-gram(1~3토큰)을 만들고,
각 n-gram의 등장 횟수를 센다. 새 프로젝트명이 오면 그 이름의 n-gram 중
"등장 횟수 × 토큰 수" 점수가 가장 높은 것을 대표 이름으로 삼는다.
- 등장이 잦을수록 대표가 될 확률이 높아진다 (예: "두레로지스"는 모든 변형에 포함되어 압도)
- 토큰 수 보정 덕에 "시스템" 같은 범용 단어 하나가 서로 다른 프로젝트를 삼키지 않는다
  (예: "정산 시스템" 5회×2 = 10 > "시스템" 8회×1 = 8)
"""
import logging
import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Claim, WikiPage  # noqa: F401

log = logging.getLogger("baton.projects")

_SKIP_SEGMENTS = {"공통", "담당자미정"}  # 프로젝트 계층이 아닌 예약 세그먼트
MAX_NGRAM = 3
MIN_COUNT_TO_MERGE = 2  # 대표 이름이 실제로 반복 등장할 때만 치환


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[\s/·,()\[\]\-_]+", (name or "").strip()) if t]


def ngrams(name: str, max_n: int = MAX_NGRAM) -> list[str]:
    """연속 n-gram (1~max_n 토큰). '두레로지스 캐싱 전략' →
    두레로지스 / 캐싱 / 전략 / 두레로지스 캐싱 / 캐싱 전략 / 두레로지스 캐싱 전략"""
    toks = _tokens(name)
    out: list[str] = []
    for n in range(1, min(max_n, len(toks)) + 1):
        for i in range(len(toks) - n + 1):
            gram = " ".join(toks[i : i + n])
            if n == 1 and len(gram) < 2:  # 한 글자 단독 토큰은 대표가 될 수 없다
                continue
            out.append(gram)
    return out


def build_counts(db: Session) -> Counter:
    """클레임의 project 값 + 기존 트리의 프로젝트 세그먼트에서 n-gram 등장 횟수 집계.
    (이름 하나당 같은 gram은 1회로 센다)"""
    c: Counter = Counter()
    names: list[str] = []
    for (proj,) in db.execute(select(Claim.project).where(Claim.project.isnot(None))):
        names.append(proj)
    for (fp,) in db.execute(select(WikiPage.folder_path)):
        parts = [p for p in (fp or "").split("/") if p]
        if len(parts) >= 2 and parts[1] not in _SKIP_SEGMENTS:
            names.append(parts[1])
    for name in names:
        for g in set(ngrams(name)):
            c[g] += 1
    return c


def canonical(name: str, counts: Counter) -> str:
    """이 이름의 n-gram 중 점수(등장 횟수 × 토큰 수)가 가장 높은 것을 대표 이름으로 고른다."""
    grams = ngrams(name)
    if not grams:
        return name
    # 점수 동률이면 더 자주 등장한 쪽(보통 더 짧은 공통 이름)이 대표가 된다
    best = max(grams, key=lambda g: (counts.get(g, 0) * len(g.split()), counts.get(g, 0)))
    if best != name and counts.get(best, 0) >= MIN_COUNT_TO_MERGE:
        return best
    return name


def normalize_folder(db: Session, folder_path: str) -> str:
    """folder_path의 프로젝트 세그먼트(2번째)를 대표 이름으로 치환."""
    parts = [p for p in (folder_path or "").split("/") if p]
    if len(parts) >= 2 and parts[1] not in _SKIP_SEGMENTS:
        canon = canonical(parts[1], build_counts(db))
        if canon != parts[1]:
            log.info("프로젝트명 정규화: %s → %s", parts[1], canon)
            parts[1] = canon
    return "/".join(parts)


def normalize_all(db: Session) -> int:
    """기존 전체 페이지의 프로젝트 세그먼트 + 클레임의 project 필드를 최신 집계 기준으로 재정규화.
    (인제스트 배치 후 호출 — 데이터가 쌓이면서 초기에 파편화됐던 이름들이 대표 이름으로 합쳐진다)"""
    counts = build_counts(db)
    moved = 0
    for page in db.scalars(select(WikiPage)).all():
        parts = [p for p in (page.folder_path or "").split("/") if p]
        if len(parts) < 2 or parts[1] in _SKIP_SEGMENTS:
            continue
        canon = canonical(parts[1], counts)
        if canon != parts[1]:
            old = page.folder_path
            parts[1] = canon
            page.folder_path = "/".join(parts)
            moved += 1
            log.info("트리 재배치: %s → %s (%s)", old, page.folder_path, page.title)
    # 클레임의 project도 같은 기준으로 — 트리와 클레임이 항상 같은 프로젝트명을 쓰게 한다
    for claim in db.scalars(select(Claim).where(Claim.project.isnot(None))).all():
        canon = canonical(claim.project, counts)
        if canon != claim.project:
            if claim.rendered_text:
                claim.rendered_text = claim.rendered_text.replace(
                    f"프로젝트: {claim.project}", f"프로젝트: {canon}")
            claim.project = canon
            moved += 1
    if moved:
        db.commit()
    return moved
