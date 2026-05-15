#!/usr/bin/env python3
"""
【模块】burn_subtitles.py — FFmpeg 硬烧字幕（subtitles filter），处理 Windows 长路径问题。
【调用方】命令行；run.py 硬烧双语成片步骤。

使用 FFmpeg 将字幕烧录进视频（复制到临时短路径以避免 Windows 路径问题）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def burn(
    video: Path,
    subtitle: Path,
    output: Path,
    ffmpeg: str = "ffmpeg",
    *,
    font_name: str | None = None,
    font_size: int | None = None,
    outline: int | None = None,
    margin_v: int | None = None,
) -> None:
    if not video.exists():
        raise FileNotFoundError(video)
    if not subtitle.exists():
        raise FileNotFoundError(subtitle)
    tmp = Path(tempfile.mkdtemp(prefix="ytburn_"))
    try:
        v = tmp / "in.mp4"
        s = tmp / "sub.srt"
        shutil.copy2(video, v)
        shutil.copy2(subtitle, s)
        outp = tmp / "out.mp4"
        sub_esc = str(s.resolve()).replace("\\", "/").replace(":", r"\:")
        fn = (
            font_name
            or os.getenv("BURN_SUBTITLE_FONT", "").strip()
            or ("Microsoft YaHei" if sys.platform == "win32" else "Arial")
        )
        fs = font_size
        if fs is None:
            try:
                fs = int(os.getenv("BURN_SUBTITLE_FONTSIZE", "16").strip() or "16")
            except ValueError:
                fs = 16
        fs = max(10, min(fs, 36))
        if outline is not None:
            ol = outline
        else:
            try:
                ol = int(os.getenv("BURN_SUBTITLE_OUTLINE", "1").strip() or "1")
            except ValueError:
                ol = 1
        ol = max(0, min(ol, 4))
        mv = margin_v
        if mv is None:
            try:
                mv = int(os.getenv("BURN_SUBTITLE_MARGINV", "18").strip() or "18")
            except ValueError:
                mv = 18
        # 硬烧：底边距（libass 底对齐时数值越小越贴近画面下沿）
        mv = max(4, min(mv, 200))
        vf = (
            f"subtitles='{sub_esc}':force_style="
            f"'Fontname={fn},Fontsize={fs},Outline={ol},Shadow=0,Alignment=2,MarginV={mv},WrapStyle=0'"
        )
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            str(v),
            "-vf",
            vf,
            "-c:a",
            "copy",
            str(outp),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr or r.stdout or "ffmpeg failed")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(outp), str(output))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("subtitle", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument(
        "--fontsize",
        type=int,
        default=None,
        help="烧录字号（默认 16，或环境变量 BURN_SUBTITLE_FONTSIZE）",
    )
    ap.add_argument(
        "--outline",
        type=int,
        default=None,
        help="描边宽度（默认 1，或 BURN_SUBTITLE_OUTLINE）",
    )
    ap.add_argument(
        "--margin-v",
        type=int,
        default=None,
        metavar="PX",
        help="底边距（越小越靠近画面底部；默认 18，或 BURN_SUBTITLE_MARGINV）",
    )
    args = ap.parse_args()
    burn(
        args.video,
        args.subtitle,
        args.output,
        ffmpeg=args.ffmpeg,
        font_size=args.fontsize,
        outline=args.outline,
        margin_v=args.margin_v,
    )
    print(str(args.output.resolve()))


if __name__ == "__main__":
    main()
