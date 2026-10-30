from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
    # P0-Lite 轻量迁移：已有 SQLite 文件补 progress_json，避免重建库。
    columns = {col["name"] for col in inspect(engine).get_columns("messages")}
    if "progress_json" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE messages ADD COLUMN progress_json TEXT DEFAULT '[]'"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: str | None, default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default
