"""content_hub 路径：复用 video_fetcher 的 pipeline 约定，并定义 publish_ready / 台账库路径。"""

from __future__ import annotations

from pathlib import Path

from video_fetcher.paths import (
    find_repo_root,
    output_processed_root,
    output_raw_root,
    pipeline_root,
)

__all__ = [
    "find_repo_root",
    "pipeline_root",
    "output_raw_root",
    "output_processed_root",
    "publish_ready_root",
    "catalog_db_path",
    "content_hub_root",
    "find_hard_burn_mp4",
]


def content_hub_root() -> Path:
    return Path(__file__).resolve().parent


def publish_ready_root(pipeline: Path | None = None) -> Path:
    return (pipeline or pipeline_root()) / "output" / "publish_ready"


def publish_ready_dir(pipeline: Path | None, video_id: str) -> Path:
    return publish_ready_root(pipeline) / video_id


def catalog_db_path() -> Path:
    data = content_hub_root() / "data"
    data.mkdir(parents=True, exist_ok=True)
    return data / "jobs.db"


def find_hard_burn_mp4(proc_dir: Path, video_id: str) -> Path | None:
    """processed/<id>/ 下主硬烧成片（含倍速副本时取基础名匹配的第一个）。"""
    base = proc_dir / video_id
    if not base.is_dir():
        return None
    preferred = base / f"{video_id}_zh_dub_hard_bilingual.mp4"
    if preferred.is_file():
        return preferred
    for p in sorted(base.glob(f"{video_id}_zh_dub_hard_bilingual*.mp4")):
        if p.is_file():
            return p
    return None
