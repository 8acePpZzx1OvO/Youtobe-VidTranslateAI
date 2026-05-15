"""
每个视频单独目录：
  raw/<video_id>/<video_id>.mp4、<video_id>.en.vtt
  processed/<video_id>/<video_id>.en.srt、…成片等

兼容旧版扁平：raw/<id>.mp4、processed/<id>.en.srt
"""

from __future__ import annotations

from pathlib import Path


def raw_nested_dir(raw_root: Path, stem: str) -> Path:
    return raw_root / stem


def proc_nested_dir(proc_root: Path, stem: str) -> Path:
    return proc_root / stem


def resolve_raw_video(raw_root: Path, stem: str) -> Path | None:
    p_nested = raw_nested_dir(raw_root, stem) / f"{stem}.mp4"
    if p_nested.exists():
        return p_nested
    p_flat = raw_root / f"{stem}.mp4"
    if p_flat.exists():
        return p_flat
    return None


def resolve_raw_vtt(raw_root: Path, stem: str) -> Path | None:
    p_nested = raw_nested_dir(raw_root, stem) / f"{stem}.en.vtt"
    if p_nested.exists():
        return p_nested
    p_flat = raw_root / f"{stem}.en.vtt"
    if p_flat.exists():
        return p_flat
    return None


def resolve_proc_base(proc_root: Path, stem: str) -> Path:
    """
    返回应存放 <stem>.en.srt 等文件的目录。
    若 processed/<stem>/ 下已有任一字幕 → 继续用该目录；
    若根目录下存在扁平 <stem>.*.srt → 旧版扁平；
    否则默认 processed/<stem>/（新布局，由调用方 mkdir）。
    """
    nested = proc_nested_dir(proc_root, stem)
    flat_en = proc_root / f"{stem}.en.srt"
    flat_zh = proc_root / f"{stem}.zh.srt"
    nested_en = nested / f"{stem}.en.srt"
    nested_zh = nested / f"{stem}.zh.srt"
    if nested_en.exists() or nested_zh.exists():
        return nested
    if flat_en.exists() or flat_zh.exists():
        return proc_root
    return nested
