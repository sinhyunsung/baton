"""관리 API: 데이터 소스·인제스트·린트·갱신안·확인 요청함·원본 뷰어."""
import os
import shutil
import threading
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_session
from ..models import Claim, ClaimWikiLink, LintIssue, LintRun, RawSource, WikiPage
from ..pipeline.ingest import (
    get_source_dir, get_source_dir_display, recover, resolve_dir,
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


@router.get("/sources/browse")
def sources_browse(prefix: str = "", db: Session = Depends(get_session)):
    """원본 문서를 경로 기반 폴더 구조로 탐색. prefix 하위의 폴더(문서 수)와 파일을 반환."""
    pre = prefix.strip("/")
    rows = db.execute(select(RawSource.id, RawSource.path, RawSource.title, RawSource.status)).all()
    folders: dict[str, int] = {}
    files = []
    for sid, path, title, status in rows:
        rest = path
        if pre:
            if not path.startswith(pre + "/"):
                continue
            rest = path[len(pre) + 1:]
        seg, _sep, tail = rest.partition("/")
        if tail:
            folders[seg] = folders.get(seg, 0) + 1
        else:
            files.append({"id": sid, "name": seg, "title": title, "status": status})
    for f in files:
        f["claims"] = db.scalar(select(func.count()).select_from(Claim).where(Claim.source_id == f["id"]))
    return {
        "prefix": pre,
        "folders": [{"name": k, "count": v} for k, v in sorted(folders.items())],
        "files": sorted(files, key=lambda x: x["name"]),
    }


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
    db.execute(text("DELETE FROM project_taxonomy WHERE NOT human_confirmed"))
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
def ingest_retry():
    """실패·중단 문서 복구 — 잡 잠금 아래 백그라운드로 (동시 재시도 레이스 방지)."""
    _run_bg("ingest", lambda db, prog: recover(db, progress=prog))
    return {"started": True}


@router.get("/ingest/docs")
def ingest_docs(status: str = "", q: str = "", page: int = 1, per: int = 20,
                db: Session = Depends(get_session)):
    """문서 목록 (페이징+검색). 기본 정렬: 처리 중 → 실패 → 완료 → 대기, 각 그룹 안에서 최신순."""
    per = max(5, min(per, 100))
    search = q
    q = select(RawSource)
    if search:
        like = f"%{search}%"
        q = q.where(or_(RawSource.title.ilike(like), RawSource.path.ilike(like)))
    if status == "active":
        q = q.where(RawSource.status.in_(["preprocessed", "claimed"]))
    elif status == "waiting":
        q = q.where(RawSource.status == "collected")
    elif status:
        q = q.where(RawSource.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    priority = case(
        (RawSource.status.in_(["preprocessed", "claimed"]), 0),  # 지금 처리 중인 것 먼저
        (RawSource.status == "failed", 1),
        (RawSource.status == "wiki_done", 2),
        else_=3,                                                  # 대기(collected)는 마지막
    )
    rows = db.scalars(
        q.order_by(priority, RawSource.id.desc())
        .offset((page - 1) * per).limit(per)
    ).all()
    return {"total": total, "page": page, "per": per, "docs": [{
        "id": s2.id, "type": s2.source_type, "title": s2.title, "path": s2.path,
        "status": s2.status, "failed_stage": s2.failed_stage, "error": s2.error,
        "claims": db.scalar(select(func.count()).select_from(Claim).where(Claim.source_id == s2.id)),
    } for s2 in rows]}


@router.get("/ingest/status")
def ingest_status(db: Session = Depends(get_session)):
    by_status = dict(db.execute(select(RawSource.status, func.count()).group_by(RawSource.status)).all())
    # 활동 중·완료 문서를 앞으로 — 병렬 배치 중엔 최신 id가 전부 "대기(collected)"라
    # 최신순만 보여주면 테이블이 얼어 보인다
    recent = db.scalars(
        select(RawSource)
        .order_by((RawSource.status == "collected").asc(), RawSource.id.desc())
        .limit(30)
    ).all()
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
    _run_bg("lint", lambda db, prog: run_lint(db, progress=prog))
    return {"started": True}


@router.get("/lint/status")
def lint_status(db: Session = Depends(get_session)):
    last = db.scalar(select(LintRun).order_by(LintRun.id.desc()).limit(1))
    return {
        "job_running": _job_state["running"],
        "job_progress": _job_state["progress"],
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


def _claim_full(db: Session, c: Claim) -> dict:
    """확인 요청함에서 A/B 비교에 쓰는 클레임 전체 정보 (출처 문서 포함)."""
    src = db.get(RawSource, c.source_id) if c.source_id else None
    return {
        "id": c.id, "text": c.rendered_text,
        "subject": c.subject, "predicate": c.predicate, "object": c.object,
        "polarity": c.polarity, "department": c.department, "owner": c.owner, "project": c.project,
        "scope": c.scope, "time_scope": c.time_scope,
        "confidence": c.confidence, "human_confirmed": c.human_confirmed,
        "superseded": c.superseded, "claim_type": c.claim_type,
        "source": {"id": src.id, "type": src.source_type, "title": src.title, "path": src.path} if src else None,
    }


@router.get("/claims")
def claims_list(q: str = "", owner: str = "", project: str = "", status: str = "",
                limit: int = 60, offset: int = 0, db: Session = Depends(get_session)):
    """클레임 브라우저: 검색·필터 (담당자/프로젝트/상태)."""
    stmt = select(Claim)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Claim.rendered_text.ilike(like), Claim.subject.ilike(like),
                              Claim.object.ilike(like), Claim.owner.ilike(like), Claim.project.ilike(like)))
    if owner:
        stmt = stmt.where(Claim.owner == owner)
    if project:
        stmt = stmt.where(Claim.project == project)
    if status == "confirmed":
        stmt = stmt.where(Claim.human_confirmed.is_(True))
    elif status == "unconfirmed":
        stmt = stmt.where(Claim.human_confirmed.is_(False), Claim.superseded.is_(False))
    elif status == "superseded":
        stmt = stmt.where(Claim.superseded.is_(True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Claim.id.desc()).limit(limit).offset(offset)).all()
    owners = [{"name": o, "count": n} for o, n in db.execute(
        select(Claim.owner, func.count()).where(Claim.owner.isnot(None))
        .group_by(Claim.owner).order_by(func.count().desc()).limit(24))]
    projects = [{"name": pj, "count": n} for pj, n in db.execute(
        select(Claim.project, func.count()).where(Claim.project.isnot(None))
        .group_by(Claim.project).order_by(func.count().desc()).limit(24))]
    return {"total": total, "items": [_claim_full(db, c) for c in rows],
            "owners": owners, "projects": projects}


@router.get("/claims/{claim_id}")
def claim_detail(claim_id: int, db: Session = Depends(get_session)):
    """클레임 상세: 전체 필드 + 연결 위키 페이지 + 관련 린트 이슈 + 유사 클레임."""
    c = db.get(Claim, claim_id)
    if not c:
        raise HTTPException(404)
    pages = []
    for l in db.scalars(select(ClaimWikiLink).where(ClaimWikiLink.claim_id == claim_id)):
        p = db.get(WikiPage, l.page_id)
        if p:
            pages.append({"id": p.id, "title": p.title, "folder_path": p.folder_path})
    issues = [{
        "id": i.id, "type": i.issue_type, "status": i.status,
        "question": i.question, "detail": i.detail, "answer": i.answer,
    } for i in db.scalars(select(LintIssue).order_by(LintIssue.id.desc()))
        if claim_id in (i.claim_ids or [])]
    rows = db.execute(
        select(Claim, Claim.embedding.cosine_distance(c.embedding).label("d"))
        .where(Claim.id != claim_id).order_by("d").limit(6)
    ).all()
    similar = [{**_claim_full(db, sc), "sim": round(1 - d, 3)} for sc, d in rows]
    return {
        **_claim_full(db, c),
        "rendered_text": c.rendered_text,
        "created_at": c.created_at.isoformat(),
        "pages": pages, "issues": issues, "similar": similar,
    }


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
            "claims": [_claim_full(db, c) for c in claims if c],
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
