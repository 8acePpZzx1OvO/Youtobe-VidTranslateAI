"""SQLite 任务台账。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from content_hub.catalog.models import JobRecord, STATUS_DISCOVERED
from content_hub.catalog.state_machine import assert_transition
from content_hub.paths import catalog_db_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or catalog_db_path())
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    source_platform TEXT NOT NULL,
                    source_video_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    error TEXT,
                    attempts INTEGER DEFAULT 0,
                    publish_status TEXT DEFAULT '{}',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_platform, source_video_id)
                )
                """
            )

    def get(self, platform: str, video_id: str) -> JobRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE source_platform=? AND source_video_id=?",
                (platform, video_id),
            ).fetchone()
        return JobRecord.from_row(dict(row)) if row else None

    def upsert_discovered(self, job: JobRecord) -> JobRecord:
        existing = self.get(job.source_platform, job.source_video_id)
        if existing:
            return existing
        now = _utc_now()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    source_platform, source_video_id, url, status, title,
                    error, attempts, publish_status, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.source_platform,
                    job.source_video_id,
                    job.url,
                    job.status or STATUS_DISCOVERED,
                    job.title,
                    job.error,
                    job.attempts,
                    json.dumps(job.publish_status, ensure_ascii=False),
                    json.dumps(job.metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get(job.source_platform, job.source_video_id) or job

    def set_status(
        self,
        platform: str,
        video_id: str,
        new_status: str,
        *,
        error: str | None = None,
        title: str | None = None,
        metadata_patch: dict | None = None,
    ) -> JobRecord:
        rec = self.get(platform, video_id)
        if not rec:
            raise KeyError(f"job not found: {platform}:{video_id}")
        assert_transition(rec.status, new_status)
        pub = rec.publish_status
        meta = dict(rec.metadata)
        if metadata_patch:
            meta.update(metadata_patch)
        attempts = rec.attempts
        if new_status != rec.status and error:
            attempts += 1
        now = _utc_now()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE jobs SET status=?, error=?, title=COALESCE(?, title),
                    attempts=?, publish_status=?, metadata=?, updated_at=?
                WHERE source_platform=? AND source_video_id=?
                """,
                (
                    new_status,
                    error,
                    title,
                    attempts,
                    json.dumps(pub, ensure_ascii=False),
                    json.dumps(meta, ensure_ascii=False),
                    now,
                    platform,
                    video_id,
                ),
            )
        return self.get(platform, video_id)  # type: ignore[return-value]

    def set_platform_publish_status(
        self,
        platform: str,
        video_id: str,
        publish_platform: str,
        publish_state: str,
    ) -> JobRecord:
        rec = self.get(platform, video_id)
        if not rec:
            raise KeyError(f"job not found: {platform}:{video_id}")
        pub = dict(rec.publish_status)
        pub[publish_platform] = publish_state
        now = _utc_now()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE jobs SET publish_status=?, updated_at=?
                WHERE source_platform=? AND source_video_id=?
                """,
                (
                    json.dumps(pub, ensure_ascii=False),
                    now,
                    platform,
                    video_id,
                ),
            )
        return self.get(platform, video_id)  # type: ignore[return-value]

    def list_by_status(self, status: str) -> list[JobRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY updated_at",
                (status,),
            ).fetchall()
        return [JobRecord.from_row(dict(r)) for r in rows]
