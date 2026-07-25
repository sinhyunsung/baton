"""인제스트 파이프라인: 수집(해시 중복 제거) → 전처리+클레임 추출(LLM 1회) → 임베딩 → 위키 갱신안."""
import hashlib
import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..config import CLAIM_SWEEP, DATA_SOURCES_DIR, PARALLEL_WORKERS
from ..db import SessionLocal
from ..models import Claim, Preprocessed, RawSource
from ..prompts.claims import (
    CHUNK_CLAIMS_SYSTEM, CHUNK_CLAIMS_USER,
    CLAIMS_SWEEP_SYSTEM, CLAIMS_SWEEP_USER,
)
from .wiki import apply_wiki_updates

log = logging.getLogger("baton.ingest")

META_KEYS_SKIP = {"content_field_names", "title_field_name", "dataset_doc_uuid"}
SUPPORTED_EXT = (".json", ".txt", ".md", ".markdown")


def resolve_dir(path: str) -> str | None:
    """사용자가 입력한 호스트 경로를 컨테이너 경로로 해석. 없으면 None."""
    path = (path or "").strip().rstrip("/")
    if not path:
        return None
    candidates = [path]
    if path.startswith("/home/shs"):
        candidates.append("/data/host" + path[len("/home/shs"):])
    candidates.append(os.path.join("/data/host", path.lstrip("/")))
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def get_source_dir(db: Session) -> str:
    from ..models import AppSetting
    row = db.get(AppSetting, "source_dir")
    return row.value if row else DATA_SOURCES_DIR


def set_source_dir(db: Session, container_dir: str, display: str):
    from ..models import AppSetting
    for key, value in (("source_dir", container_dir), ("source_dir_display", display)):
        row = db.get(AppSetting, key)
        if row:
            row.value = value
        else:
            db.add(AppSetting(key=key, value=value))
    db.commit()


def get_source_dir_display(db: Session) -> str:
    from ..models import AppSetting
    row = db.get(AppSetting, "source_dir_display")
    if row:
        return row.value
    return "데이터셋 (기본)"


def scan_files(base_dir: str) -> list[str]:
    out = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
        for f in sorted(files):
            if f.lower().endswith(SUPPORTED_EXT) and f != "agents.md":
                out.append(os.path.join(root, f))
    return out


def collect_one(db: Session, filepath: str, base_dir: str) -> RawSource | None:
    """원본 불변 저장. 동일 해시 스킵, 동일 경로+다른 해시는 새 버전."""
    raw_bytes = open(filepath, "rb").read()
    sha = hashlib.sha256(raw_bytes).hexdigest()
    if db.scalar(select(RawSource).where(RawSource.sha256 == sha)):
        return None  # 동일 해시 → 스킵

    rel = os.path.relpath(filepath, base_dir)
    parts = rel.split(os.sep)
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".json":
        try:
            content = json.loads(raw_bytes)
        except ValueError:
            return None
        source_type = parts[0] if len(parts) > 1 else "local"
        title_field = content.get("title_field_name")
        title = content.get("title") or (content.get(title_field) if title_field else None)
    else:  # txt / md — 텍스트 파일을 데이터셋과 같은 형태로 감싼다
        text = raw_bytes.decode("utf-8", errors="replace")
        content = {"content": text, "content_field_names": ["content"], "file_ext": ext}
        source_type = parts[0] if len(parts) > 1 else "local"
        title = os.path.splitext(os.path.basename(rel))[0]

    prev_versions = db.scalars(select(RawSource).where(RawSource.path == rel)).all()
    version = max((r.version for r in prev_versions), default=0) + 1
    src = RawSource(
        source_type=source_type, path=rel, content=content, sha256=sha,
        version=version, title=str(title) if title else os.path.basename(rel),
    )
    db.add(src)
    db.commit()
    return src


def extract_body(content: dict) -> tuple[str, dict]:
    """데이터셋 JSON에서 본문과 메타데이터 분리. content_field_names가 본문 필드를 가리킨다."""
    fields = content.get("content_field_names") or ["content"]
    body = "\n\n".join(str(content.get(f, "")) for f in fields).strip()
    meta = {k: v for k, v in content.items() if k not in set(fields) | META_KEYS_SKIP}
    return body, meta


def process_source(db: Session, src: RawSource) -> int:
    """전처리+클레임 추출+임베딩+위키 갱신안. 반환: 저장된 클레임 수."""
    try:
        # 재처리 멱등성: 이전 시도의 산출물(클레임·링크·조각)을 지우고 처음부터
        # — 클레임 저장 후 실패한 문서를 재시도할 때 중복 적재를 막는다
        old = db.scalars(select(Claim).where(Claim.source_id == src.id)).all()
        if old:
            from ..models import ClaimWikiLink
            ids = [c.id for c in old]
            db.query(ClaimWikiLink).filter(ClaimWikiLink.claim_id.in_(ids)).delete(synchronize_session=False)
            db.query(Claim).filter(Claim.id.in_(ids)).delete(synchronize_session=False)
            db.query(Preprocessed).filter(Preprocessed.raw_source_id == src.id).delete(synchronize_session=False)
            db.commit()
            log.info("재처리 정리: %s 클레임 %d개 삭제 후 재추출", src.path, len(ids))

        body, meta = extract_body(src.content)
        if len(body.strip()) < 15:
            src.failed_stage = "collected"
            src.status = "failed"
            src.error = "본문이 비어있거나 너무 짧음(15자 미만)"
            db.commit()
            return 0

        # ① 시맨틱 청킹 — 의미 급변(주제 환기) 지점이 경계. 청크가 곧 전처리 조각이 된다
        from .chunking import semantic_chunks
        chunks = semantic_chunks(body[:24000])
        db.add(Preprocessed(raw_source_id=src.id,
                            chunks=[{"type": "text", "text": c} for c in chunks]))
        src.status = "preprocessed"
        db.commit()

        # ② 청크별 추출 — 메타데이터는 그대로, "이미 뽑힌 클레임"을 이어 전달해 표기 통일
        claims_data: list[dict] = []
        seen_keys: set[tuple[str, str]] = set()
        for idx, chunk in enumerate(chunks, 1):
            result = llm.extract_json(
                CHUNK_CLAIMS_SYSTEM,
                CHUNK_CLAIMS_USER.format(
                    title=src.title or src.path, source_type=src.source_type, path=src.path,
                    meta=json.dumps(meta, ensure_ascii=False)[:1500],
                    existing="\n".join(f"- {_render(c)}" for c in claims_data[-40:]) or "(아직 없음)",
                    idx=idx, total=len(chunks), body=chunk,
                ),
            )
            for c in result.get("claims", []):
                key = (str(c.get("subject", "")).strip(), str(c.get("object", "")).strip())
                if c.get("subject") and c.get("object") and key not in seen_keys:
                    claims_data.append(c)
                    seen_keys.add(key)

        # 누락 스윕(2차 패스): 본문을 다시 훑어 아직 안 뽑힌 사실만 추가 — 재현율 우선
        if CLAIM_SWEEP and claims_data:
            try:
                seen = seen_keys
                sweep = llm.extract_json(CLAIMS_SWEEP_SYSTEM, CLAIMS_SWEEP_USER.format(
                    existing="\n".join(f"- {_render(c)}" for c in claims_data), body=body[:12000]))
                added = 0
                for c in sweep.get("claims", []):
                    key = (str(c.get("subject", "")).strip(), str(c.get("object", "")).strip())
                    if c.get("subject") and c.get("object") and key not in seen:
                        claims_data.append(c)
                        seen.add(key)
                        added += 1
                if added:
                    log.info("누락 스윕: +%d개 (%s)", added, src.path)
            except Exception:  # noqa: BLE001 — 스윕 실패는 치명적이지 않다
                log.exception("누락 스윕 실패(무시): %s", src.path)

        # project는 위키 트리의 프로젝트 선정 로직(n-gram 빈도 canonical)과 같은 기준으로 정규화해 저장한다
        if any(c.get("project") for c in claims_data):
            from .projects import build_counts, canonical
            from .taxonomy import resolve_project
            from ..models import ProjectTaxonomy
            counts = build_counts(db)
            tax = {t.raw_tag: t.canonical for t in db.scalars(select(ProjectTaxonomy))}
            for c in claims_data:
                if c.get("project"):
                    c["project"] = resolve_project(canonical(str(c["project"]), counts), tax)
        claims: list[Claim] = []
        if claims_data:
            rendered = [_render(c) for c in claims_data]
            vecs = llm.embed(rendered, input_type="search_document")
            sv, pv, ov = _element_vecs(claims_data)
            for c, text, vec, s_v, p_v, o_v in zip(claims_data, rendered, vecs, sv, pv, ov):
                claims.append(Claim(
                    subject=c["subject"], predicate=c.get("predicate", ""),
                    object=str(c["object"]), polarity=c.get("polarity", "positive"),
                    scope=c.get("scope"), time_scope=c.get("time_scope"),
                    claim_type=c.get("claim_type", "rule"),
                    owner=(c.get("owner") or None), project=(c.get("project") or None),
                    confidence=float(c.get("confidence", 0.5)),
                    source_id=src.id, rendered_text=text, embedding=vec,
                    subject_vec=s_v, predicate_vec=p_v, object_vec=o_v,
                ))
            db.add_all(claims)
        src.status = "claimed"
        db.commit()

        if claims:
            apply_wiki_updates(db, src, claims)
        src.status = "wiki_done"
        db.commit()
        return len(claims)
    except Exception as e:  # noqa: BLE001
        log.exception("인제스트 실패: %s", src.path)
        db.rollback()
        src.failed_stage = src.status if src.status != "failed" else src.failed_stage
        src.status = "failed"
        src.error = str(e)[:500]
        db.commit()
        return 0


def _element_vecs(claims_data: list[dict]) -> tuple[list, list, list]:
    """주어·속성·값을 각각 임베딩 (중복 문자열은 1회만 호출)."""
    subs = [str(c.get("subject", "")) for c in claims_data]
    preds = [str(c.get("predicate", "")) for c in claims_data]
    objs = [str(c.get("object", "")) for c in claims_data]
    uniq = sorted({t for t in subs + preds + objs if t})
    vecs = llm.embed(uniq, input_type="search_document") if uniq else []
    vmap = dict(zip(uniq, vecs))
    empty = None
    return ([vmap.get(t, empty) for t in subs],
            [vmap.get(t, empty) for t in preds],
            [vmap.get(t, empty) for t in objs])


def _render(c: dict) -> str:
    parts = [f"{c['subject']}의 {c.get('predicate', '')}은(는) {c['object']}"]
    if c.get("polarity") == "negative":
        parts.append("아니다")
    extras = [x for x in (c.get("scope"), c.get("time_scope")) if x]
    if c.get("project"):
        extras.append(f"프로젝트: {c['project']}")
    if c.get("owner"):
        extras.append(f"담당: {c['owner']}")
    if extras:
        parts.append(f"({', '.join(extras)})")
    return " ".join(parts)


def run_ingest(db: Session, limit: int | None = None,
               base_dir: str | None = None, progress: dict | None = None) -> dict:
    """지정 폴더 스캔 배치. limit로 처리 문서 수 제한(비용 가드).
    progress 딕셔너리를 주면 실시간 현황(스캔/수집/처리/클레임/실패/현재 파일)을 갱신한다."""
    base = base_dir or get_source_dir(db)
    files = scan_files(base)
    stats = {"scanned": len(files), "new": 0, "processed": 0, "skipped": 0,
             "claims": 0, "failed": 0, "dir": base}
    prog = progress if progress is not None else {}
    started = time.time()
    prog.update(scanned=len(files), target=limit, new=0, processed=0,
                claims=0, failed=0, skipped=0, current=None, elapsed_sec=0)
    # ① 수집은 직렬 (빠른 IO — sha256 중복 판정 순서 보장)
    new_ids: list[int] = []
    for fp in files:
        if limit is not None and len(new_ids) >= limit:
            break
        src = collect_one(db, fp, base)
        if src is None:
            stats["skipped"] += 1
            prog["skipped"] = stats["skipped"]
            continue
        new_ids.append(src.id)
    # 재개(resume): 이전 배치가 중단되며 남긴 미완료 문서를 이어받는다
    # (수집됨 = 대기 중 끊김, preprocessed/claimed = 처리 중 끊김 — 멱등 재처리로 안전)
    stuck = db.scalars(
        select(RawSource.id).where(
            RawSource.status.in_(["collected", "preprocessed", "claimed"]),
            ~RawSource.id.in_(new_ids or [0]),
        )
    ).all()
    if stuck:
        log.info("중단 배치 재개: %d건 이어서 처리", len(stuck))
        new_ids.extend(stuck)
        stats["resumed"] = len(stuck)

    stats["new"] = len(new_ids)
    prog["new"] = stats["new"]

    # ② LLM 처리(추출·스윕·위키)는 문서 단위 병렬 — 병목이 LLM 대기라 효과가 크다
    lock = threading.Lock()
    active: list[str] = []

    def worker(src_id: int) -> tuple[int, bool]:
        wdb = SessionLocal()
        try:
            wsrc = wdb.get(RawSource, src_id)
            with lock:
                active.append(wsrc.title or wsrc.path)
                prog["current"] = " · ".join(active[:3]) + (f" 외 {len(active)-3}" if len(active) > 3 else "")
            n = process_source(wdb, wsrc)
            failed = wsrc.status == "failed"
            with lock:
                if (wsrc.title or wsrc.path) in active:
                    active.remove(wsrc.title or wsrc.path)
            log.info("[병렬] %s → 클레임 %d (%s)", wsrc.path, n, wsrc.status)
            return n, failed
        finally:
            wdb.close()

    if new_ids:
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
            futures = [pool.submit(worker, sid) for sid in new_ids]
            for fut in as_completed(futures):
                try:
                    n, failed = fut.result()
                except Exception:  # noqa: BLE001
                    log.exception("병렬 인제스트 워커 실패")
                    n, failed = 0, True
                with lock:
                    stats["claims"] += n
                    stats["processed"] += 1
                    if failed:
                        stats["failed"] += 1
                    prog.update(processed=stats["processed"], claims=stats["claims"],
                                failed=stats["failed"],
                                current=" · ".join(active[:3]) or None,
                                elapsed_sec=round(time.time() - started))
    # 배치가 끝나면 쌓인 집계로 기존 트리의 프로젝트명도 재정규화 (파편 폴더 병합)
    from .projects import normalize_all
    from .wiki import dedupe_pages, refresh_all_links

    stats["project_merged"] = normalize_all(db)
    from .taxonomy import update_taxonomy
    stats["taxonomy"] = update_taxonomy(db)
    stats["pages_merged"] = dedupe_pages(db)
    stats["links_refreshed"] = refresh_all_links(db)
    stats["duration_sec"] = round(time.time() - started)
    prog.update(elapsed_sec=stats["duration_sec"], done=True)
    return stats


def _cos(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def resweep_source(db: Session, src: RawSource) -> int:
    """이미 인제스트된 문서에 누락 스윕을 재실행 — 옛 프롬프트가 놓친 사실을 소급 추출한다.
    새 클레임은 기존 클레임이 연결된 페이지들에 친화도 기반으로 연결된다."""
    body, _meta = extract_body(src.content)
    if len(body.strip()) < 15:
        return 0
    existing = db.scalars(select(Claim).where(Claim.source_id == src.id)).all()
    sweep = llm.extract_json(CLAIMS_SWEEP_SYSTEM, CLAIMS_SWEEP_USER.format(
        existing="\n".join(f"- {c.rendered_text}" for c in existing) or "(없음)",
        body=body[:12000]))
    seen = {(c.subject.strip(), c.object.strip()) for c in existing}
    new_data = []
    for c in sweep.get("claims", []):
        key = (str(c.get("subject", "")).strip(), str(c.get("object", "")).strip())
        if c.get("subject") and c.get("object") and key not in seen:
            new_data.append(c)
            seen.add(key)
    if not new_data:
        return 0
    from .projects import build_counts, canonical
    counts = build_counts(db)
    for c in new_data:
        if c.get("project"):
            c["project"] = canonical(str(c["project"]), counts)
    rendered = [_render(c) for c in new_data]
    vecs = llm.embed(rendered, input_type="search_document")
    sv, pv, ov = _element_vecs(new_data)
    # 표현만 바꾼 중복 차단: 기존 클레임과 임베딩 유사도 0.93 이상이면 같은 사실로 보고 버린다
    existing_vecs = [list(c.embedding) for c in existing if c.embedding is not None]
    claims = []
    for c, text, vec, s_v, p_v, o_v in zip(new_data, rendered, vecs, sv, pv, ov):
        if existing_vecs and max(_cos(vec, ev) for ev in existing_vecs) >= 0.93:
            continue
        claims.append(Claim(
            subject=c["subject"], predicate=c.get("predicate", ""),
            object=str(c["object"]), polarity=c.get("polarity", "positive"),
            scope=c.get("scope"), time_scope=c.get("time_scope"),
            claim_type=c.get("claim_type", "rule"),
            owner=(c.get("owner") or None), project=(c.get("project") or None),
            confidence=float(c.get("confidence", 0.5)),
            source_id=src.id, rendered_text=text, embedding=vec,
            subject_vec=s_v, predicate_vec=p_v, object_vec=o_v,
        ))
    if not claims:
        return 0
    db.add_all(claims)
    db.commit()
    # 이 소스의 기존 클레임이 붙어있는 페이지들에 새 클레임을 친화도로 연결
    from ..models import ClaimWikiLink, WikiPage
    from .wiki import _reconcile_links
    pages = {}
    for ec in existing:
        for l in db.scalars(select(ClaimWikiLink).where(ClaimWikiLink.claim_id == ec.id)):
            page = db.get(WikiPage, l.page_id)
            if page:
                pages[page.id] = page
    _reconcile_links(db, claims, list(pages.values()))
    log.info("재스윕: %s → +%d개", src.path, len(claims))
    return len(claims)


def retry_failed(db: Session) -> int:
    failed = db.scalars(select(RawSource).where(RawSource.status == "failed")).all()
    ok = 0
    for src in failed:
        src.error = None
        src.failed_stage = None
        src.status = "collected"
        if process_source(db, src):
            ok += 1
    return ok
