"""从 sources.yaml 订阅展开 FetchJob 列表。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from video_fetcher.paths import find_repo_root
from video_fetcher.sources.base import FetchJob
from video_fetcher.sources.youtube import expand_youtube

from content_hub.config_loader import load_filters_config, load_sources_config
from content_hub.discovery.filters import job_passes_filters

logger = logging.getLogger(__name__)


def _resolve_target(target: str, config_dir: Path) -> str:
    p = Path(target)
    if p.is_file():
        return str(p.resolve())
    repo = find_repo_root(config_dir)
    for base in (config_dir, repo):
        c = (base / target).resolve()
        if c.is_file():
            return str(c)
    return target


def discover_jobs(
    sources_config_path: str | Path,
    *,
    filters_override: dict[str, Any] | None = None,
) -> list[FetchJob]:
    """读取订阅源，展开并过滤，返回待入队任务。"""
    cfg = load_sources_config(sources_config_path)
    config_dir = Path(cfg["_config_path"]).parent
    filters = filters_override if filters_override is not None else load_filters_config(cfg)

    jobs: list[FetchJob] = []
    seen: set[str] = set()

    for sub in cfg.get("subscriptions") or []:
        if not sub.get("enabled", True):
            continue
        target = sub.get("target")
        if not target:
            continue
        limit = sub.get("limit")
        resolved = _resolve_target(str(target), config_dir)
        batch = expand_youtube(resolved, limit=limit)
        for job in batch:
            vid = job.video_id or ""
            if not vid:
                continue
            if vid in seen:
                continue
            ok, reason = job_passes_filters(job, filters)
            if not ok:
                logger.info("过滤跳过 %s: %s", vid, reason)
                continue
            seen.add(vid)
            jobs.append(job)

    logger.info("发现 %d 条可处理视频", len(jobs))
    return jobs
