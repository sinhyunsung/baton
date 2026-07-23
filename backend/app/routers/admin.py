"""관리 API: 데이터 소스·인제스트·린트·갱신안·확인 요청함·원본 뷰어."""
import os
import shutil
import threading
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_session
from ..models import Claim, LintIssue, LintRun, RawSource, WikiPage
from ..pipeline.ingest import (
    get_source_dir, get_source_dir_display, resolve_dir, retry_failed,
    run_ingest, scan_files, set_source_dir,
)
from ..pipeline.lint import answer_issue, run_lint

router = APIRouter(prefix="/api", tags=["admin"])

_job_lock = threading.Lock()
_job_state = {"running": None, "progress": {}}  # progress: 실행 중 배치의 실시간 현황


def _run_bg(kind: str, fn):
    """fn(db, progress_dict) 형태. progress_dict는 상태 API로 실시간 노출된다."""
    if not _job_lock.acquire(blocking=False):
        raise HTTPException(409, "다른 작업이 실행 중입니다")
    _job_state["running"] = kind
    _job_state["progress"] = {}

    def worker():
        db = SessionLocal()
        try:
            fn(db, _job_state["progress"])
        finally:
            db.close()
            _job_state["running"] = None
            _job_lock.release()

    threading.Thread(target=worker, daemon=True).start()


# ---------- 데이터 소스 ----------
class SourceDirIn(BaseModel):
    dir: str


@router.get("/sources/summary")
def sources_summary(db: Session = Depends(get_session)):
    rows = db.execute(
        select(RawSource.source_type, func.count()).group_by(RawSource.source_type)
    ).all()
    cur_dir = get_source_dir(db)
    try:
        total_files = len(scan_files(cur_dir))
    except OSError:
        total_files = 0
    return {
        "collected": {t: n for t, n in rows},
        "total_collected": sum(n for _, n in rows),
        "total_files_on_disk": total_files,
        "source_dir": get_source_dir_display(db),
        "job_running": _job_state["running"],
    }


@router.post("/sources/config")
def sources_config(body: SourceDirIn, db: Session = Depends(get_session)):
    resolved = resolve_dir(body.dir)
    if not resolved:
        raise HTTPException(400, "폴더를 찾을 수 없습니다. 호스트 경로(/home/shs/...)를 확인하세요.")
    n = len(scan_files(resolved))
    set_source_dir(db, resolved, body.dir.strip())
    return {"dir": body.dir.strip(), "files_found": n}


@router.get("/sources/{source_id}")
def source_detail(source_id: int, db: Session = Depends(get_session)):
    s = db.get(RawSource, source_id)
    if not s:
        raise HTTPException(404)
    claims = db.scalars(select(Claim).where(Claim.source_id == source_id)).all()
    return {
        "id": s.id, "type": s.source_type, "path": s.path, "title": s.title,
        "sha256": s.sha256, "version": s.version, "status": s.status,
        "ingested_at": s.ingested_at.isoformat(), "content": s.content,
        "claims": [{"id": c.id, "text": c.rendered_text, "claim_type": c.claim_type,
                    "confidence": c.confidence, "human_confirmed": c.human_confirmed}
                   for c in claims],
    }


# ---------- 데이터 리셋 ----------
@router.post("/admin/reset")
def reset_all(db: Session = Depends(get_session)):
    """모든 수집 데이터·클레임·위키·이슈를 삭제한다 (설정은 유지). 실행 중이면 거부."""
    if _job_state["running"]:
        raise HTTPException(409, "작업 실행 중에는 리셋할 수 없습니다")
    db.execute(text(
        "TRUNCATE preprocessed, claim_wiki_link, wiki_versions, "
        "lint_issues, lint_runs, claims, wiki_pages, raw_sources RESTART IDENTITY CASCADE"
    ))
    db.commit()
    return {"reset": True}


# ---------- 인제스트 (브라우저 업로드) ----------
UPLOAD_ROOT = "/tmp/baton_uploads"


def _safe_relpath(p: str) -> str | None:
    p = (p or "").replace("\\", "/").lstrip("/")
    parts = [seg for seg in p.split("/") if seg not in ("", ".", "..") and not seg.startswith(".")]
    return "/".join(parts) or None


@router.post("/ingest/upload")
def ingest_upload(
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(...),
    limit: int | None = Form(None),
):
    """접속자 PC에서 올린 파일/폴더를 스테이징에 저장 후 인제스트 배치 시작."""
    if len(files) != len(paths):
        raise HTTPException(400, "files/paths 개수가 다릅니다")
    batch_dir = os.path.join(UPLOAD_ROOT, f"upload_{int(time.time())}")
    rels = [_safe_relpath(p) for p in paths]
    # 공통 최상위 폴더(예: 'sources/')는 벗겨서 1단계 하위 폴더가 소스 타입이 되게 한다
    firsts = {r.split("/")[0] for r in rels if r}
    strip_top = len(firsts) == 1 and any(r and "/" in r for r in rels)
    saved = 0
    for f, rel in zip(files, rels):
        if not rel:
            continue
        if strip_top:
            rel = rel.split("/", 1)[1] if "/" in rel else rel
        dest = os.path.join(batch_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved += 1
    if saved == 0:
        raise HTTPException(400, "저장된 파일이 없습니다")

    _run_bg("ingest", lambda db, prog: run_ingest(db, limit=limit, base_dir=batch_dir, progress=prog))
    return {"started": True, "uploaded": saved, "limit": limit}


class IngestIn(BaseModel):
    limit: int | None = 20


@router.post("/ingest/scan")
def ingest_scan(body: IngestIn):
    _run_bg("ingest", lambda db, prog: run_ingest(db, limit=body.limit, progress=prog))
    return {"started": True, "limit": body.limit}


@router.post("/ingest/retry")
def ingest_retry(db: Session = Depends(get_session)):
    return {"retried_ok": retry_failed(db)}


@router.get("/ingest/status")
def ingest_status(db: Session = Depends(get_session)):
    by_status = dict(db.execute(select(RawSource.status, func.count()).group_by(RawSource.status)).all())
    recent = db.scalars(select(RawSource).order_by(RawSource.id.desc()).limit(30)).all()
    n_claims = db.scalar(select(func.count()).select_from(Claim))
    return {
        "job_running": _job_state["running"],
        "job_progress": _job_state["progress"],
        "by_status": by_status,
        "total_claims": n_claims,
        "recent": [{
            "id": s.id, "type": s.source_type, "title": s.title, "path": s.path,
            "status": s.status, "failed_stage": s.failed_stage, "error": s.error,
            "claims": db.scalar(select(func.count()).select_from(Claim).where(Claim.source_id == s.id)),
        } for s in recent],
    }


# ---------- 린트 ----------
@router.post("/lint/run")
def lint_run():
    _run_bg("lint", lambda db, prog: run_lint(db))
    return {"started": True}


@router.get("/lint/status")
def lint_status(db: Session = Depends(get_session)):
    last = db.scalar(select(LintRun).order_by(LintRun.id.desc()).limit(1))
    return {
        "job_running": _job_state["running"],
        "last_run": None if not last else {
            "id": last.id, "pages_checked": last.pages_checked, "llm_calls": last.llm_calls,
            "contradictions": last.contradictions, "gaps": last.gaps,
            "started_at": last.started_at.isoformat(),
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
        },
    }


# ---------- 확인 요청함 ----------
class AnswerIn(BaseModel):
    answer: str


@router.get("/lint/issues")
def lint_issues(status: str = "open", db: Session = Depends(get_session)):
    rows = db.scalars(
        select(LintIssue).where(LintIssue.status == status).order_by(LintIssue.id.desc())
    ).all()
    out = []
    for i in rows:
        page = db.get(WikiPage, i.page_id) if i.page_id else None
        claims = [db.get(Claim, cid) for cid in (i.claim_ids or [])]
        out.append({
            "id": i.id, "type": i.issue_type, "question": i.question, "detail": i.detail,
            "options": i.options, "assignee": i.assignee_name, "status": i.status,
            "answer": i.answer, "created_at": i.created_at.isoformat(),
            "page": {"id": page.id, "title": page.title} if page else None,
            "claims": [{"id": c.id, "text": c.rendered_text,
                        "source_id": c.source_id, "confidence": c.confidence}
                       for c in claims if c],
        })
    counts = dict(db.execute(
        select(LintIssue.issue_type, func.count())
        .where(LintIssue.status == "open").group_by(LintIssue.issue_type)).all())
    return {"issues": out, "open_counts": counts}


@router.post("/lint/issues/{issue_id}/answer")
def lint_issue_answer(issue_id: int, body: AnswerIn, db: Session = Depends(get_session)):
    try:
        issue = answer_issue(db, issue_id, body.answer)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": issue.id, "status": issue.status}
