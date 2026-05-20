"""content_hub 端到端编排：发现 → 译配 → publish_ready → 发布。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from content_hub.catalog.models import (
    STATUS_PUBLISH_READY,
    STATUS_PUBLISHED,
    JobRecord,
)
from content_hub.catalog.store import JobStore
from content_hub.config_loader import (
    load_platforms_config,
    load_publish_rules,
    load_sources_config,
)
from content_hub.discovery.feeds import discover_jobs
from content_hub.paths import catalog_db_path, content_hub_root
from content_hub.process.workflow_bridge import job_from_fetch, process_job
from content_hub.publish.coordinator import publish_job
from video_fetcher.sources.youtube import extract_video_id

logger = logging.getLogger(__name__)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    hub = content_hub_root()
    load_dotenv(hub / ".env", override=False)
    from video_fetcher.paths import pipeline_root

    load_dotenv(pipeline_root() / ".env", override=False)


def run_once(
    config_path: str | Path,
    *,
    discover_only: bool = False,
    skip_pipeline: bool = False,
    skip_publish: bool = False,
    limit: int | None = None,
    db_path: Path | None = None,
) -> int:
    _load_env()
    if os.environ.get("CONTENT_HUB_DB_PATH"):
        db = Path(os.environ["CONTENT_HUB_DB_PATH"])
    else:
        db = db_path or catalog_db_path()

    load_sources_config(config_path)  # validate early
    publish_rules = load_publish_rules(load_sources_config(config_path))
    platforms_cfg = load_platforms_config(load_sources_config(config_path))

    store = JobStore(db)
    fetch_jobs = discover_jobs(config_path)
    if limit is not None and limit > 0:
        fetch_jobs = fetch_jobs[: int(limit)]

    exit_code = 0
    for fj in fetch_jobs:
        vid = fj.video_id or extract_video_id(fj.url) or ""
        if not vid:
            logger.warning("跳过无 video_id: %s", fj.url)
            continue
        rec = job_from_fetch(fj.url, vid, fj.title)
        existing = store.upsert_discovered(rec)
        if discover_only:
            logger.info("discovered %s", vid)
            continue
        if existing.status == STATUS_PUBLISHED:
            logger.info("已发布，跳过 %s", vid)
            continue

        if existing.status == STATUS_PUBLISH_READY and skip_pipeline:
            pass
        elif existing.status != STATUS_PUBLISH_READY or not skip_pipeline:
            rc, _ = process_job(
                existing,
                store,
                publish_rules=publish_rules,
                skip_pipeline=skip_pipeline,
            )
            if rc != 0:
                exit_code = rc
                continue

        if skip_publish:
            continue

        updated = store.get("youtube", vid)
        if updated and updated.status == STATUS_PUBLISH_READY:
            prc = publish_job(updated, store, platforms_cfg=platforms_cfg, publish_rules=publish_rules)
            if prc != 0:
                exit_code = prc

    return exit_code


def publish_ready_only(
    config_path: str | Path,
    *,
    db_path: Path | None = None,
) -> int:
    _load_env()
    cfg = load_sources_config(config_path)
    publish_rules = load_publish_rules(cfg)
    platforms_cfg = load_platforms_config(cfg)
    store = JobStore(db_path or catalog_db_path())
    jobs = store.list_by_status(STATUS_PUBLISH_READY)
    code = 0
    for job in jobs:
        if publish_job(job, store, platforms_cfg=platforms_cfg, publish_rules=publish_rules) != 0:
            code = 1
    return code
