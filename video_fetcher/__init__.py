"""
【模块】video_fetcher — 批量拉取 YouTube 到 pipeline 约定目录，可选触发译配。
"""

from video_fetcher.paths import (
    find_repo_root,
    output_processed_root,
    output_raw_root,
    pipeline_root,
)
from video_fetcher.sources.base import FetchJob, FetchResult
from video_fetcher.sources.youtube import YouTubeSource, expand_youtube, is_youtube_url

__all__ = [
    "FetchJob",
    "FetchResult",
    "YouTubeSource",
    "expand_youtube",
    "find_repo_root",
    "is_youtube_url",
    "output_processed_root",
    "output_raw_root",
    "pipeline_root",
]
