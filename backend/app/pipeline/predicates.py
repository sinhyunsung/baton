"""속성 사전: predicate마다 값이 몇 개일 수 있는지(cardinality)를 기억한다.

원칙 (프로젝트 택소노미와 동일):
- 사전을 미리 채우지 않는다. 모르면 unknown이고, unknown이면 모순으로 단정하는 대신 역질문을 낸다.
- 역질문 답변이 사전을 채운다 — "둘 다 맞음"이면 many, 하나를 고르면 one.
- 사람이 답한 항목(human_confirmed)은 자동 로직이 다시 바꾸지 않는다.

판정표 (컨플루언스 「클레임에 속성사전 추가 초안」):
  one     + 값 다름          → 모순 후보 (기존 흐름 그대로 LLM 판정)
  many    + 둘 다 add        → 공존. 후보에서 제외
  many    + 한쪽이 set       → 모순 후보 (배타 주장이 깨짐)
  unknown + 한쪽이 set       → 모순 후보 (사전 없이도 배타 주장은 충돌)
  unknown + 둘 다 add        → LLM 판정에 "clarify" 허용 — 같은 속성의 다른 값이면 모순 단정 대신 역질문
  remove가 끼면              → 모순 후보 (인계인지 이탈인지 LLM 판정에 맡김)

주의: unknown이라고 LLM을 건너뛰고 바로 역질문을 내면 안 된다. 규칙 깔때기는 "이호준 vs
Week 3~4 일정" 같은 무관한 쌍을 다 통과시키는데, 그 쌍을 걸러주던 것이 LLM 판정이었다.
실측(문서 3건 + 기존 코퍼스)에서 직행 역질문 27건 중 25건이 이런 소음이었다.
"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PredicateAttribute

log = logging.getLogger("baton.predicates")

VERDICT, SKIP, CLARIFY = "verdict", "skip", "clarify"


def normalize_predicate(p) -> str:
    return str(p or "").strip()


def get_cardinality(db: Session, predicate) -> str:
    """사전에서 이 속성의 값 개수 성질을 찾는다. 없으면 unknown — 그게 기본 상태다."""
    key = normalize_predicate(predicate)
    if not key:
        return "unknown"
    row = db.scalar(select(PredicateAttribute).where(PredicateAttribute.predicate == key))
    return row.cardinality if row else "unknown"


def cardinality_gate(cardinality: str, a_assertion: str, b_assertion: str) -> str:
    """값이 다른 쌍(owner·value 충돌 후보)을 어디로 보낼지 정한다. 순수 함수 — 테스트 대상."""
    asserts = {a_assertion or "add", b_assertion or "add"}
    if "remove" in asserts:
        return VERDICT
    if "set" in asserts:
        return VERDICT          # 배타 주장이 끼면 사전과 무관하게 충돌 후보
    # 여기부터는 둘 다 add
    if cardinality == "many":
        return SKIP             # 값 여럿 허용 + 둘 다 "하나 더" → 공존
    if cardinality == "one":
        return VERDICT
    return CLARIFY              # unknown — 단정하지 말고 사람에게


def record_answer(db: Session, predicate, many: bool) -> None:
    """역질문 답변으로 사전을 채운다. 사전은 역질문의 입력이 아니라 산출물이다."""
    key = normalize_predicate(predicate)
    if not key:
        return
    row = db.scalar(select(PredicateAttribute).where(PredicateAttribute.predicate == key))
    if row is None:
        row = PredicateAttribute(predicate=key)
        db.add(row)
    wanted = "many" if many else "one"
    if row.human_confirmed and row.cardinality != wanted:
        # 사람 판정이 이미 있는데 반대 답이 오면 덮지 않고 기록만 — 흔들리는 속성은 unknown이 안전
        log.warning("속성 사전 상충: %s 기존=%s 새 답=%s — unknown으로 되돌림", key, row.cardinality, wanted)
        row.cardinality = "unknown"
        row.human_confirmed = False
    else:
        row.cardinality = wanted
        row.human_confirmed = True
    row.answers = (row.answers or 0) + 1
    row.updated_at = datetime.utcnow()
