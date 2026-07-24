"""CLI: python -m app.cli ingest --limit 10 / lint / retry"""
import logging

import typer

from .db import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
cli = typer.Typer(help="Baton 파이프라인 CLI")


@cli.command()
def ingest(limit: int = typer.Option(None, help="처리할 신규 문서 수 제한(비용 가드)"),
           dir: str = typer.Option(None, help="인제스트할 로컬 폴더 (미지정 시 화면에서 설정한 폴더)")):
    init_db()
    db = SessionLocal()
    try:
        from .pipeline.ingest import resolve_dir, run_ingest
        base = None
        if dir:
            base = resolve_dir(dir)
            if not base:
                typer.echo(f"폴더를 찾을 수 없음: {dir}")
                raise typer.Exit(1)
        stats = run_ingest(db, limit=limit, base_dir=base)
        typer.echo(f"완료: {stats}")
    finally:
        db.close()


@cli.command()
def lint():
    init_db()
    db = SessionLocal()
    try:
        from .pipeline.lint import run_lint
        run = run_lint(db)
        typer.echo(f"린트 완료: 페이지 {run.pages_checked}, LLM {run.llm_calls}회, "
                   f"모순 {run.contradictions}, 빈틈 {run.gaps}")
    finally:
        db.close()


@cli.command()
def retry():
    init_db()
    db = SessionLocal()
    try:
        from .pipeline.ingest import retry_failed
        typer.echo(f"재시도 성공: {retry_failed(db)}건")
    finally:
        db.close()


@cli.command()
def resweep(source_id: int = typer.Option(None, help="특정 문서만"),
            everything: bool = typer.Option(False, "--all", help="완료된 전체 문서")):
    """기존 문서에 누락 스윕 재실행 — 옛 추출이 놓친 클레임을 소급 보완."""
    init_db()
    db = SessionLocal()
    try:
        from sqlalchemy import select

        from .models import RawSource
        from .pipeline.ingest import resweep_source
        if source_id:
            docs = [db.get(RawSource, source_id)]
        elif everything:
            docs = db.scalars(select(RawSource).where(RawSource.status == "wiki_done")).all()
        else:
            typer.echo("--source-id 또는 --all 지정")
            raise typer.Exit(1)
        total = 0
        for src in docs:
            if not src:
                continue
            try:
                n = resweep_source(db, src)
            except Exception as e:  # noqa: BLE001 — 한 문서 실패가 전체를 멈추지 않게
                typer.echo(f"  {src.path}: 실패 ({str(e)[:60]})")
                db.rollback()
                continue
            total += n
            typer.echo(f"  {src.path}: +{n}")
        from .pipeline.projects import normalize_all
        from .pipeline.wiki import refresh_all_links
        merged = normalize_all(db)
        refreshed = refresh_all_links(db)
        typer.echo(f"재스윕 완료: +{total}개 클레임 (프로젝트 재정규화 {merged}건, 링크 재생성 {refreshed}건)")
    finally:
        db.close()


if __name__ == "__main__":
    cli()
