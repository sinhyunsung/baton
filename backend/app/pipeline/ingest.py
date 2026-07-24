"""인제스트 파이프라인: 수집(해시 중복 제거) → 전처리+클레임 추출(LLM 1회) → 임베딩 → 위키 갱신안."""
import hashlib
import json
import logging
import os
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..config import CLAIM_SWEEP, DATA_SOURCES_DIR
from ..models import Claim, Preprocessed, RawSource
from ..prompts.claims import (
    CLAIMS_SWEEP_SYSTEM, CLAIMS_SWEEP_USER,
    PREPROCESS_AND_CLAIMS_SYSTEM, PREPROCESS_AND_CLAIMS_USER,
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
        body, meta = extract_body(src.content)
        if len(body.strip()) < 15:
            src.failed_stage = "collected"
            src.status = "failed"
            src.error = "본문이 비어있거나 너무 짧음(15자 미만)"
            db.commit()
            return 0

        result = llm.extract_json(
            PREPROCESS_AND_CLAIMS_SYSTEM,
            PREPROCESS_AND_CLAIMS_USER.format(
                source_type=src.source_type, path=src.path,
                meta=json.dumps(meta, ensure_ascii=False)[:1500], body=body[:12000],
            ),
        )
        db.add(Preprocessed(raw_source_id=src.id, chunks=result.get("chunks", [])))
        src.status = "preprocessed"
        db.commit()

        claims_data = [c for c in result.get("claims", []) if c.get("subject") and c.get("object")]

        # 누락 스윕(2차 패스): 본문을 다시 훑어 아직 안 뽑힌 사실만 추가 — 재현율 우선
        if CLAIM_SWEEP and claims_data:
            try:
                seen = {(c["subject"].strip(), str(c["object"]).strip()) for c in claims_data}
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
            counts = build_counts(db)
            for c in claims_data:
                if c.get("project"):
                    c["project"] = canonical(str(c["project"]), counts)
        claims: list[Claim] = []
        if claims_data:
            rendered = [_render(c) for c in claims_data]
            vecs = llm.embed(rendered, input_type="search_document")
            for c, text, vec in zip(claims_data, rendered, vecs):
                claims.append(Claim(
                    subject=c["subject"], predicate=c.get("predicate", ""),
                    object=str(c["object"]), polarity=c.get("polarity", "positive"),
                    scope=c.get("scope"), time_scope=c.get("time_scope"),
                    claim_type=c.get("claim_type", "rule"),
                    owner=(c.get("owner") or None), project=(c.get("project") or None),
                    confidence=float(c.get("confidence", 0.5)),
                    source_id=src.id, rendered_text=text, embedding=vec,
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
    prog.update(scanned=len(files), target=limit, new=0, processed=0,
                claims=0, failed=0, skipped=0, current=None)
    for fp in files:
        if limit is not None and stats["new"] >= limit:
            break
        src = collect_one(db, fp, base)
        if src is None:
            stats["skipped"] += 1
            prog["skipped"] = stats["skipped"]
            continue
        stats["new"] += 1
        prog.update(new=stats["new"], current=src.title or src.path)
        n = process_source(db, src)
        stats["claims"] += n
        stats["processed"] += 1
        if src.status == "failed":
            stats["failed"] += 1
        prog.update(processed=stats["processed"], claims=stats["claims"],
                    failed=stats["failed"], current=None)
        log.info("[%d/%s] %s → 클레임 %d (%s)", stats["new"], limit or "∞", src.path, n, src.status)
    # 배치가 끝나면 쌓인 집계로 기존 트리의 프로젝트명도 재정규화 (파편 폴더 병합)
    from .projects import normalize_all
    from .wiki import refresh_all_links

    stats["project_merged"] = normalize_all(db)
    stats["links_refreshed"] = refresh_all_links(db)
    return stats


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
