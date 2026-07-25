"""증거 기반 프로젝트 택소노미.

원칙: 이름의 의미 유사도는 병합 근거가 될 수 없다 ("배송채널 API"는 어느 프로젝트에나 있을 수 있다).
병합은 문서가 들고 온 증거로만 한다:

① 위성 흡수 (공출현): 태그 A가 등장하는 문서의 60%+ 에서 상위 태그 B가 함께 등장하면 A→B.
   통합 문서 1건이 두 실제 프로젝트를 잘못 합치지 않도록 "지배적 공출현"만 인정한다.
② 범용 태그 재배정: "API"·"시스템"·부서명처럼 어디에나 나오는 태그는 프로젝트가 아니다.
   그 태그를 단 클레임마다 같은 문서의 구체 태그를 다수결로 물려받는다.
③ 간접 증거: 담당자 겹침(≥2명) + 클레임 내용 근접(코사인 ≥0.75)이 모두 성립할 때만 자동 병합.
④ 애매 구간: 자동 병합 금지 — 확인 요청함에 역질문을 올리고 사람 답변을 사전에 영구 기록한다.

사전(project_taxonomy)은 sticky: 한번 정해진 대표는 병합 이벤트 없이 바뀌지 않고,
human_confirmed 항목은 자동 로직이 절대 건드리지 않는다.
"""
import logging
import math
from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Claim, LintIssue, ProjectTaxonomy, WikiPage

log = logging.getLogger("baton.taxonomy")

COOCCUR_DOMINANCE = 0.6   # A 문서 중 B와 함께인 비율이 이 이상이어야 위성
COOCCUR_MIN_DOCS = 2      # 문서 1건짜리 공출현은 증거가 아니다 (부분 데이터 오병합 방지)
OWNER_OVERLAP_MIN = 2     # 간접 증거: 공유 담당자 최소 수
CONTENT_SIM_MIN = 0.75    # 간접 증거: 클레임 평균 임베딩 코사인
ASK_SIM_RANGE = (0.65, 0.75)  # 이 구간이면 사람에게 물어본다
MAX_QUESTIONS_PER_RUN = 5


def _cos(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def _load_pinned(db: Session) -> dict[str, str]:
    return {t.raw_tag: t.canonical for t in db.scalars(select(ProjectTaxonomy))}


def _save_entry(db: Session, raw: str, canonical: str, evidence: str, human: bool = False):
    row = db.scalar(select(ProjectTaxonomy).where(ProjectTaxonomy.raw_tag == raw))
    if row:
        if row.human_confirmed and not human:
            return  # 사람 판정은 자동 로직이 못 바꾼다
        row.canonical, row.evidence, row.updated_at = canonical, evidence, datetime.utcnow()
        row.human_confirmed = row.human_confirmed or human
    else:
        db.add(ProjectTaxonomy(raw_tag=raw, canonical=canonical, evidence=evidence, human_confirmed=human))


def _dept_names(db: Session) -> set[str]:
    out = {"미지정"}
    for (fp,) in db.execute(select(WikiPage.folder_path)):
        parts = [p for p in (fp or "").split("/") if p]
        if parts:
            out.add(parts[0])
    return out


def update_taxonomy(db: Session) -> dict:
    """배치 후 실행: 증거를 수집해 사전을 갱신하고 클레임·폴더에 적용한다."""
    stats = {"absorbed": 0, "generic_reassigned": 0, "owner_merged": 0,
             "questions": 0, "claims_updated": 0, "pages_updated": 0}

    rows = db.execute(
        select(Claim.id, Claim.source_id, Claim.project, Claim.owner)
        .where(Claim.project.isnot(None))
    ).all()
    if not rows:
        return stats

    doc_tags: dict[int, set[str]] = defaultdict(set)      # 문서 → 태그들
    tag_docs: dict[str, set[int]] = defaultdict(set)      # 태그 → 문서들
    tag_owners: dict[str, set[str]] = defaultdict(set)    # 태그 → 담당자들
    for _cid, sid, proj, owner in rows:
        doc_tags[sid].add(proj)
        tag_docs[proj].add(sid)
        if owner:
            tag_owners[proj].add(owner)

    # 문서 제목도 공출현 증거로 쓴다: 제목에 기존 태그가 문자 그대로 들어있으면
    # 그 문서의 태그 집합에 합류 ("정산개편 운영 매뉴얼" 제목 → '정산' 클레임들이 '정산개편'과 공출현)
    from ..models import RawSource
    vocab = sorted(tag_docs, key=len, reverse=True)
    for src in db.scalars(select(RawSource).where(RawSource.id.in_(list(doc_tags)))):
        title = src.title or ""
        for t in vocab:
            if len(t) >= 3 and t in title and t not in doc_tags[src.id]:
                doc_tags[src.id].add(t)
                tag_docs[t].add(src.id)

    pinned_rows = {t.raw_tag: t for t in db.scalars(select(ProjectTaxonomy))}
    mapping: dict[str, str] = {r.raw_tag: r.canonical for r in pinned_rows.values()}

    def rep(tag: str, _seen=None) -> str:
        seen = _seen or set()
        while tag in mapping and mapping[tag] != tag and tag not in seen:
            seen.add(tag)
            tag = mapping[tag]
        return tag

    depts = _dept_names(db)
    generic = {t for t in tag_docs
               if (len(t.split()) == 1 and len(tag_docs[t]) >= 3
                   and t not in {rep(x) for x in mapping}) or t in depts}

    # ── ① 위성 흡수: A 문서의 60%+ 에서 더 큰 B가 함께 등장 ──────────────────
    changed = True
    while changed:
        changed = False
        for a in sorted(tag_docs, key=lambda t: len(tag_docs[t])):
            if a in generic or (a in pinned_rows and pinned_rows[a].human_confirmed):
                continue
            if rep(a) != a:
                continue
            host_count: Counter = Counter()
            for sid in tag_docs[a]:
                for b in doc_tags[sid]:
                    if b != a and rep(b) != a and b not in generic:
                        host_count[rep(b)] += 1
            if not host_count:
                continue
            host, n = host_count.most_common(1)[0]
            if (n >= COOCCUR_MIN_DOCS
                    and n / len(tag_docs[a]) >= COOCCUR_DOMINANCE
                    and len(tag_docs.get(host, set())) > len(tag_docs[a])):
                mapping[a] = host
                _save_entry(db, a, host, "cooccur")
                stats["absorbed"] += 1
                changed = True
                log.info("위성 흡수: %s → %s (공출현 %d/%d 문서)", a, host, n, len(tag_docs[a]))

    # ── ③ 간접 증거: 담당자 겹침 + 내용 근접 (독립 태그 vs 대표들) ───────────
    reps = {rep(t) for t in tag_docs} - generic
    centroids: dict[str, list] = {}

    def centroid(tag: str) -> list | None:
        if tag not in centroids:
            vecs = [list(c.embedding) for c in db.scalars(
                select(Claim).where(Claim.project == tag).limit(30)) if c.embedding is not None]
            centroids[tag] = [sum(col) / len(vecs) for col in zip(*vecs)] if vecs else None
        return centroids[tag]

    open_tax_pairs = {frozenset((i.meta or {}).get("pair", []))
                      for i in db.scalars(select(LintIssue).where(
                          LintIssue.issue_type == "taxonomy", LintIssue.status == "open"))}
    for a in sorted(tag_docs, key=lambda t: len(tag_docs[t])):
        if rep(a) != a or a in generic or len(tag_docs[a]) > 2:
            continue  # 신생·소수 문서 태그만 검토
        if a in pinned_rows and pinned_rows[a].human_confirmed:
            continue
        best, best_shared, best_sim = None, 0, 0.0
        for b in reps:
            if b == a or len(tag_docs.get(b, set())) <= len(tag_docs[a]):
                continue
            shared = len(tag_owners[a] & tag_owners.get(b, set()))
            if shared == 0:
                continue
            ca, cb = centroid(a), centroid(b)
            sim = _cos(ca, cb) if ca and cb else 0.0
            if (shared, sim) > (best_shared, best_sim):
                best, best_shared, best_sim = b, shared, sim
        if not best:
            continue
        if best_shared >= OWNER_OVERLAP_MIN and best_sim >= CONTENT_SIM_MIN:
            mapping[a] = best
            _save_entry(db, a, best, "owners")
            stats["owner_merged"] += 1
            log.info("증거 병합: %s → %s (담당자 %d명 공유, 내용 유사 %.2f)", a, best, best_shared, best_sim)
        elif best_sim >= ASK_SIM_RANGE[0] and stats["questions"] < MAX_QUESTIONS_PER_RUN:
            if frozenset((a, best)) in open_tax_pairs:
                continue
            db.add(LintIssue(
                issue_type="taxonomy", page_id=None, claim_ids=[],
                question=f"'{a}'은(는) '{best}' 프로젝트 소속인가요?",
                detail=f"[프로젝트 분류] 담당자 {best_shared}명 공유, 내용 유사도 {best_sim:.0%} — "
                       f"자동 병합 기준에는 못 미쳐 확인이 필요합니다.",
                options=[f"예 — '{best}'로 병합", f"아니오 — '{a}' 별개 유지"],
                meta={"kind": "taxonomy", "pair": [a, best]},
            ))
            open_tax_pairs.add(frozenset((a, best)))
            stats["questions"] += 1

    db.commit()

    # ── ② 범용 태그 재배정: 클레임마다 같은 문서의 구체 태그 다수결 ──────────
    for g in generic:
        for c in db.scalars(select(Claim).where(Claim.project == g)).all():
            siblings = [rep(t) for t in doc_tags.get(c.source_id, set()) if t != g and rep(t) not in generic]
            if not siblings:
                continue
            new = Counter(siblings).most_common(1)[0][0]
            if c.rendered_text:
                c.rendered_text = c.rendered_text.replace(f"프로젝트: {c.project}", f"프로젝트: {new}")
            c.project = new
            stats["generic_reassigned"] += 1
    db.commit()

    # ── 사전 매핑을 클레임·페이지 폴더에 적용 ────────────────────────────────
    final = {t: rep(t) for t in list(tag_docs) if rep(t) != t}
    if final:
        for c in db.scalars(select(Claim).where(Claim.project.in_(list(final)))).all():
            new = final[c.project]
            if c.rendered_text:
                c.rendered_text = c.rendered_text.replace(f"프로젝트: {c.project}", f"프로젝트: {new}")
            c.project = new
            stats["claims_updated"] += 1
        for p in db.scalars(select(WikiPage)).all():
            parts = [seg for seg in (p.folder_path or "").split("/") if seg]
            if len(parts) >= 2 and parts[1] in final:
                parts[1] = final[parts[1]]
                p.folder_path = "/".join(parts)
                stats["pages_updated"] += 1
        db.commit()
    return stats


def resolve_project(name: str, taxonomy_map: dict[str, str]) -> str:
    """인제스트 시 사전 조회 (n-gram canonical 다음 단계)."""
    seen = set()
    while name in taxonomy_map and taxonomy_map[name] != name and name not in seen:
        seen.add(name)
        name = taxonomy_map[name]
    return name


def answer_taxonomy(db: Session, issue: LintIssue, answer: str) -> None:
    """확인 요청함의 분류 질문 답변 처리: 사람 판정을 사전에 영구 고정."""
    a, b = (issue.meta or {}).get("pair", [None, None])
    if not a or not b:
        return
    if answer.strip().startswith("예") or b in answer:
        _save_entry(db, a, b, "human", human=True)
        # 즉시 적용
        for c in db.scalars(select(Claim).where(Claim.project == a)).all():
            if c.rendered_text:
                c.rendered_text = c.rendered_text.replace(f"프로젝트: {a}", f"프로젝트: {b}")
            c.project = b
        for p in db.scalars(select(WikiPage)).all():
            parts = [seg for seg in (p.folder_path or "").split("/") if seg]
            if len(parts) >= 2 and parts[1] == a:
                parts[1] = b
                p.folder_path = "/".join(parts)
        log.info("분류 확정(사람): %s → %s", a, b)
    else:
        _save_entry(db, a, a, "human", human=True)  # 별개 유지로 고정 — 다시 안 물어봄
        log.info("분류 확정(사람): %s 별개 유지", a)
    db.commit()
