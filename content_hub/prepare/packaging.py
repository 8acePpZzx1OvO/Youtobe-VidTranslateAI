"""组装 publish_ready 目录：manifest + 视频/字幕链接或复制。"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from content_hub.paths import find_hard_burn_mp4, publish_ready_dir
from video_fetcher.paths import output_processed_root, pipeline_root

logger = logging.getLogger(__name__)


def build_publish_package(
    video_id: str,
    manifest: dict[str, Any],
    *,
    pipeline: Path | None = None,
    copy_video: bool = False,
) -> Path:
    """写入 pipeline/output/publish_ready/<id>/。"""
    pipe = pipeline or pipeline_root()
    proc = output_processed_root(pipe)
    out = publish_ready_dir(pipe, video_id)
    out.mkdir(parents=True, exist_ok=True)

    hard = find_hard_burn_mp4(proc, video_id)
    if not hard or not hard.is_file():
        raise FileNotFoundError(f"未找到硬烧成片: {video_id}")

    dest_video = out / "video.mp4"
    if copy_video:
        shutil.copy2(hard, dest_video)
    else:
        if dest_video.exists() or dest_video.is_symlink():
            dest_video.unlink(missing_ok=True)
        try:
            dest_video.symlink_to(hard.resolve())
        except OSError:
            shutil.copy2(hard, dest_video)

    bi = proc / video_id / f"{video_id}.bilingual.srt"
    dest_subs = out / "subtitles.srt"
    if bi.is_file():
        if copy_video:
            shutil.copy2(bi, dest_subs)
        else:
            if dest_subs.exists() or dest_subs.is_symlink():
                dest_subs.unlink(missing_ok=True)
            try:
                dest_subs.symlink_to(bi.resolve())
            except OSError:
                shutil.copy2(bi, dest_subs)

    manifest_path = out / "manifest.json"
    manifest.setdefault("video_id", video_id)
    manifest.setdefault("video_path", str(hard))
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("publish_ready: %s", out)
    return out
