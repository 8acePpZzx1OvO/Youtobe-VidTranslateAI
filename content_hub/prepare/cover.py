"""封面生成占位（后续可用 FFmpeg 截帧）。"""

from __future__ import annotations

from pathlib import Path


def ensure_cover_placeholder(out_dir: Path) -> Path | None:
    """若尚无 cover.jpg 则跳过（不强制）。"""
    cover = out_dir / "cover.jpg"
    return cover if cover.is_file() else None
