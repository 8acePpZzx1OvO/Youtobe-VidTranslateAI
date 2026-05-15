"""
【模块】youtobe_layout.py — 目录布局约定（raw/processed 嵌套与扁平兼容）与成片后精简输出工具。
【调用方】run.py、finish_outputs.py、minimize_pipeline_outputs（若启用）等 import。

每个视频单独目录：
  raw/<video_id>/<video_id>.mp4、<video_id>.en.vtt
  processed/<video_id>/<video_id>.en.srt、…成片等

兼容旧版扁平：raw/<id>.mp4、processed/<id>.en.srt

精简布局（搬运归档用）：见 minimize_raw_dir / minimize_proc_dir。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def minimize_outputs_enabled() -> bool:
    """默认开启精简；YOUTOBE_MINIMIZE_OUTPUTS=0 关闭。"""
    v = (os.getenv("YOUTOBE_MINIMIZE_OUTPUTS") or "1").strip().lower()
    return v not in ("0", "false", "no", "off", "")


def _unlink_any(path: Path) -> None:
    try:
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def minimize_raw_dir(raw_root: Path, stem: str) -> None:
    """
    raw 仅保留原视频：<stem>.mp4。
    删除同目录下的 .vtt、.json、封面等下载附属文件。
    """
    nested = raw_nested_dir(raw_root, stem)
    mp4 = nested / f"{stem}.mp4"
    if mp4.exists():
        for p in list(nested.iterdir()):
            if p == mp4:
                continue
            _unlink_any(p)
        return
    flat = raw_root / f"{stem}.mp4"
    if not flat.exists():
        return
    for p in list(raw_root.iterdir()):
        if p == flat:
            continue
        name = p.name
        if name.startswith(f"{stem}.") or name.startswith(f"{stem}_"):
            _unlink_any(p)


def minimize_proc_dir(proc_base: Path, stem: str) -> None:
    """
    processed 仅保留：
      <stem>.en.srt、<stem>.zh.srt、<stem>_zh_dub_hard_bilingual*.mp4（含倍速导出副本）。
    """
    if not proc_base.is_dir():
        return
    for p in list(proc_base.iterdir()):
        if p.is_dir():
            _unlink_any(p)
            continue
        if not p.is_file():
            continue
        n = p.name
        if n in (f"{stem}.en.srt", f"{stem}.zh.srt"):
            continue
        if n.startswith(f"{stem}_zh_dub_hard_bilingual") and n.endswith(".mp4"):
            continue
        _unlink_any(p)


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
