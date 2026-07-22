"""CLI: python -m app.cli ingest --limit 10 / lint / retry"""
import logging

import typer

from .db import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
cli = typer.Typer(help="Baton 파이프라인 CLI")


@cli.command()
def ingest(limit: int = typer.Option(None, help="처리할 신규 문서 수 제한(비용 가드)"),
           auto_approve: bool = typer.Option(False, help="위키 갱신안 자동 승인(대량 인제스트용)"),
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
        stats = run_ingest(db, limit=limit, auto_approve=auto_approve, base_dir=base)
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
def retry(auto_approve: bool = False):
    init_db()
    db = SessionLocal()
    try:
        from .pipeline.ingest import retry_failed
        typer.echo(f"재시도 성공: {retry_failed(db, auto_approve=auto_approve)}건")
    finally:
        db.close()


if __name__ == "__main__":
    cli()
