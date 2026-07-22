from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import EMBED_DIM


class Base(DeclarativeBase):
    pass


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class RawSource(Base):
    __tablename__ = "raw_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True)  # slack, google_drive, ...
    path: Mapped[str] = mapped_column(Text, index=True)
    content: Mapped[dict] = mapped_column(JSON)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # 파이프라인 상태: collected → preprocessed → claimed → wiki_done / failed
    status: Mapped[str] = mapped_column(String(20), default="collected")
    failed_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 실패 당시 마지막 완료 단계
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    claims: Mapped[list["Claim"]] = relationship(back_populates="source")


class Preprocessed(Base):
    __tablename__ = "preprocessed"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_source_id: Mapped[int] = mapped_column(ForeignKey("raw_sources.id"), index=True)
    chunks: Mapped[list] = mapped_column(JSON)  # [{type: text|table|image, text, original}]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(Text)
    predicate: Mapped[str] = mapped_column(Text)
    object: Mapped[str] = mapped_column(Text)
    polarity: Mapped[str] = mapped_column(String(10), default="positive")
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_type: Mapped[str] = mapped_column(String(10), default="rule")  # rule | event
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    human_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)  # 모순 판정에서 밀린 클레임
    source_id: Mapped[int] = mapped_column(ForeignKey("raw_sources.id"), index=True)
    rendered_text: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(EMBED_DIM))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    source: Mapped[RawSource] = relationship(back_populates="claims")


class WikiPage(Base):
    __tablename__ = "wiki_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    folder_path: Mapped[str] = mapped_column(Text, index=True)  # 예: 재무팀/경비 정산
    page_type: Mapped[str] = mapped_column(String(10), default="concept")  # concept|entity|topic
    body: Mapped[str] = mapped_column(Text)  # markdown
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    lint_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WikiVersion(Base):
    __tablename__ = "wiki_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("wiki_pages.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    cause: Mapped[str] = mapped_column(String(20))  # ingest | lint | answer | edit
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ClaimWikiLink(Base):
    __tablename__ = "claim_wiki_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("wiki_pages.id"), index=True)


class WikiProposal(Base):
    __tablename__ = "wiki_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int | None] = mapped_column(ForeignKey("wiki_pages.id"), nullable=True)  # None=신규
    new_title: Mapped[str] = mapped_column(Text)
    new_folder_path: Mapped[str] = mapped_column(Text)
    new_page_type: Mapped[str] = mapped_column(String(10), default="concept")
    new_body: Mapped[str] = mapped_column(Text)
    cause: Mapped[str] = mapped_column(String(20))  # ingest | lint | answer
    evidence_claim_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|approved|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LintIssue(Base):
    __tablename__ = "lint_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_type: Mapped[str] = mapped_column(String(16))  # contradiction | gap
    page_id: Mapped[int | None] = mapped_column(ForeignKey("wiki_pages.id"), nullable=True)
    claim_ids: Mapped[list] = mapped_column(JSON, default=list)
    question: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[list] = mapped_column(JSON, default=list)  # 답변 후보 값들
    assignee_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|answered|resolved
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LintRun(Base):
    __tablename__ = "lint_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    pages_checked: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    contradictions: Mapped[int] = mapped_column(Integer, default=0)
    gaps: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
