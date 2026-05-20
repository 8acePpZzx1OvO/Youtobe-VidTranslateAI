"""
【模块】video_fetcher.sources.base — 视频源抽象：解析 URL、展开列表、单条下载任务。
【调用方】sources.youtube；batch 队列按 FetchJob 调度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class FetchJob:
    """单条待下载任务。"""

    url: str
    video_id: str | None = None
    title: str = ""
    source: str = "youtube"


@dataclass
class FetchResult:
    """download 成功后的元数据（与 scripts/download.py 返回字段对齐）。"""

    video_id: str
    video_path: str
    subtitle_path: str | None
    title: str
    duration: float | int
    url: str = ""
    skipped: bool = False
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "video_path": self.video_path,
            "subtitle_path": self.subtitle_path,
            "title": self.title,
            "duration": self.duration,
            "url": self.url,
            "skipped": self.skipped,
            **self.extra,
        }


class VideoSource(Protocol):
    """可解析 URL 并展开为 FetchJob 列表的源。"""

    def can_handle(self, url: str) -> bool: ...

    def expand(self, target: str) -> list[FetchJob]: ...

    def fetch(
        self,
        job: FetchJob,
        raw_root: Path,
        *,
        force: bool = False,
        max_retries: int = 3,
    ) -> FetchResult: ...
