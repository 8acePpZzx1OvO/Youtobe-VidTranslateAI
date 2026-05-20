"""桥接 video_fetcher.workflow：译配并生成 publish_ready。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from video_fetcher.sources.youtube import extract_video_id
from video_fetcher.workflow import run_workflow_single

from content_hub.catalog.models import (
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_PUBLISH_READY,
    STATUS_QUEUED,
    JobRecord,
)
from content_hub.catalog.store import JobStore
from content_hub.config_loader import load_publish_rules
from content_hub.localize.metadata import build_publish_metadata
from content_hub.prepare.packaging import build_publish_package

logger = logging.getLogger(__name__)


def process_job(
    job: JobRecord,
    store: JobStore,
    *,
    publish_rules: dict[str, Any],
    skip_pipeline: bool = False,
) -> tuple[int, dict[str, Any]]:
    """
    单条：queued → processing → publish_ready（或 failed）。
    skip_pipeline=True 时仅打包已有 processed 产物。
    """
    vid = job.source_video_id
    platform = job.source_platform

    try:
        store.set_status(platform, vid, STATUS_QUEUED)
        store.set_status(platform, vid, STATUS_PROCESSING)

        if not skip_pipeline:
            rc, info = run_workflow_single(job.url)
            if rc != 0:
                store.set_status(
                    platform,
                    vid,
                    STATUS_FAILED,
                    error=f"workflow rc={rc}",
                )
                return rc, info

        title = job.title or vid
        meta = build_publish_metadata(
            title=title,
            source_url=job.url,
            channel=job.metadata.get("channel", ""),
            rules=publish_rules,
        )
        pkg = build_publish_package(vid, meta)
        store.set_status(
            platform,
            vid,
            STATUS_PUBLISH_READY,
            title=meta.get("title") or title,
            metadata_patch={"publish_ready": str(pkg)},
        )
        return 0, {"video_id": vid, "publish_ready": str(pkg), "manifest": meta}
    except Exception as e:
        logger.exception("process_job failed %s", vid)
        store.set_status(platform, vid, STATUS_FAILED, error=str(e))
        return 1, {"video_id": vid, "error": str(e)}


def job_from_fetch(url: str, video_id: str, title: str = "") -> JobRecord:
    return JobRecord(
        source_platform="youtube",
        source_video_id=video_id or (extract_video_id(url) or ""),
        url=url,
        title=title,
    )
