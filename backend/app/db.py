from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .config import DATABASE_URL, PARALLEL_WORKERS

engine = create_engine(
    DATABASE_URL,
    pool_size=PARALLEL_WORKERS + 10,   # 워커마다 세션 1개 + 여유 (API 요청·폴링용)
    max_overflow=20,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401 — 테이블 등록

    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    models.Base.metadata.create_all(engine)
    # create_all은 기존 테이블에 새 컬럼을 못 더한다 — 데이터를 지우지 않고 붙이기 위한 최소 처치
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE claims ADD COLUMN IF NOT EXISTS assertion VARCHAR(8) DEFAULT 'add'"))
        conn.commit()


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
