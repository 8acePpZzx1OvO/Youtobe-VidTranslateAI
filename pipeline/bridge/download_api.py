"""video_fetcher 兼容的 download() 接口。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from bridge.env_sync import apply_proxy_from_env, sync_env_to_config
from bridge.runner import run_download, _extract_video_id

logger = logging.getLogger(__name__)


def download(url: str, output_dir: Path, *, overwrite: bool = False) -> dict:
    """
    与旧 pipeline/scripts/download.py 返回字段对齐。
    写入 output_dir/<video_id>/<video_id>.mp4
    """
    apply_proxy_from_env()
    sync_env_to_config()

    vid = _extract_video_id(url) or "unknown"
    nested = output_dir / vid
    if nested.is_dir() and (nested / f"{vid}.mp4").is_file() and not overwrite:
        mp4 = nested / f"{vid}.mp4"
        return {
            "video_id": vid,
            "video_path": str(mp4),
            "subtitle_path": None,
            "title": "",
            "duration": 0,
            "url": url,
        }

    src = run_download(url)
    nested.mkdir(parents=True, exist_ok=True)
    dest = nested / f"{src.stem}.mp4"
    if dest.name != f"{vid}.mp4":
        dest = nested / f"{vid}.mp4"
    shutil.copy2(src, dest)

    return {
        "video_id": vid,
        "video_path": str(dest),
        "subtitle_path": None,
        "title": src.stem,
        "duration": 0,
        "url": url,
    }
