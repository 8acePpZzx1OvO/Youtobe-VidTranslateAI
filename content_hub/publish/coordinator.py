"""对 publish_ready 任务执行多平台发布。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from content_hub.catalog.models import (
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_PUBLISHING,
    JobRecord,
)
from content_hub.catalog.store import JobStore
from content_hub.paths import publish_ready_dir, pipeline_root
from content_hub.publish.bilibili.adapter import BilibiliPublisher
from content_hub.publish.weixin_channels.adapter import WeixinChannelsPublisher

logger = logging.getLogger(__name__)

_PUBLISHERS = {
    "bilibili": BilibiliPublisher,
    "weixin_channels": WeixinChannelsPublisher,
}


def publish_job(
    job: JobRecord,
    store: JobStore,
    *,
    platforms_cfg: dict[str, Any],
    publish_rules: dict[str, Any],
) -> int:
    pipe = pipeline_root()
    out = publish_ready_dir(pipe, job.source_video_id)
    manifest_path = out / "manifest.json"
    if not manifest_path.is_file():
        store.set_status(
            job.source_platform,
            job.source_video_id,
            STATUS_FAILED,
            error="manifest missing",
        )
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    store.set_status(job.source_platform, job.source_video_id, STATUS_PUBLISHING)

    platforms = platforms_cfg.get("platforms") or {}
    all_ok = True
    for key, cls in _PUBLISHERS.items():
        pcfg = platforms.get(key) or {}
        if not pcfg.get("enabled", True):
            continue
        publisher = cls(pcfg)
        result = publisher.publish(out, manifest, publish_rules)
        state = "published" if result.success else "failed"
        if result.dry_run and result.success:
            state = "dry_run_ok"
        store.set_platform_publish_status(
            job.source_platform,
            job.source_video_id,
            key,
            state,
        )
        if not result.success:
            all_ok = False
            logger.error("%s publish failed: %s", key, result.message)

    if all_ok:
        store.set_status(job.source_platform, job.source_video_id, STATUS_PUBLISHED)
        return 0
    store.set_status(
        job.source_platform,
        job.source_video_id,
        STATUS_FAILED,
        error="one or more platforms failed",
    )
    return 1
