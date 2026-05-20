"""任务台账数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

STATUS_DISCOVERED = "discovered"
STATUS_QUEUED = "queued"
STATUS_DOWNLOADING = "downloading"
STATUS_PROCESSING = "processing"
STATUS_PUBLISH_READY = "publish_ready"
STATUS_PUBLISHING = "publishing"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"

TERMINAL_STATUSES = frozenset({STATUS_PUBLISHED, STATUS_FAILED})


@dataclass
class JobRecord:
    source_platform: str
    source_video_id: str
    url: str
    status: str = STATUS_DISCOVERED
    title: str = ""
    error: str | None = None
    attempts: int = 0
    publish_status: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return f"{self.source_platform}:{self.source_video_id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> JobRecord:
        import json

        pub = row.get("publish_status") or "{}"
        meta = row.get("metadata") or "{}"
        if isinstance(pub, str):
            pub = json.loads(pub) if pub else {}
        if isinstance(meta, str):
            meta = json.loads(meta) if meta else {}
        return cls(
            source_platform=row["source_platform"],
            source_video_id=row["source_video_id"],
            url=row["url"],
            status=row.get("status") or STATUS_DISCOVERED,
            title=row.get("title") or "",
            error=row.get("error"),
            attempts=int(row.get("attempts") or 0),
            publish_status=dict(pub),
            metadata=dict(meta),
        )
