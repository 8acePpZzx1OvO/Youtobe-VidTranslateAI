#!/usr/bin/env python3
"""
【模块】mux_dub_subs.py — ffmpeg 封装：视频轨 + 中文配音轨 + 可选软字幕轨 → 单文件 MP4。
【调用方】命令行；run.py / finish_outputs 成片链路调用。

将视频画面 + 中文配音音轨 + 可选双语字幕（软字幕 mov_text）封装为单个 MP4。
若软字幕封装失败，则仅输出画面+配音，并在 stderr 提示保留外挂 .srt。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stderr or "") + (p.stdout or "")


def mux(
    video: Path,
    audio: Path,
    out: Path,
    *,
    bilingual_srt: Path | None = None,
    ffmpeg: str = "ffmpeg",
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if bilingual_srt and bilingual_srt.exists():
        tmp = Path(tempfile.mkdtemp(prefix="ytmux_"))
        try:
            s = tmp / "sub.srt"
            shutil.copy2(bilingual_srt, s)
            cmd = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-i",
                str(video),
                "-i",
                str(audio),
                "-i",
                str(s),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-map",
                "2:s:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-c:s",
                "mov_text",
                "-metadata:s:s:0",
                "language=zho",
                "-disposition:s:0",
                "default",
                "-shortest",
                str(out),
            ]
            code, err = _run(cmd)
            if code != 0:
                print(
                    f"软字幕封装失败，改输出无字幕轨成片。详情:\n{err}",
                    file=sys.stderr,
                )
                _mux_av_only(ffmpeg, video, audio, out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        _mux_av_only(ffmpeg, video, audio, out)


def _mux_av_only(ffmpeg: str, video: Path, audio: Path, out: Path) -> None:
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        str(video),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-shortest",
        str(out),
    ]
    code, err = _run(cmd)
    if code != 0:
        raise RuntimeError(err or "ffmpeg mux failed")


def main() -> None:
    ap = argparse.ArgumentParser(description="视频 + 中文配音 + 可选软字幕")
    ap.add_argument("video", type=Path)
    ap.add_argument("audio", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--subs", type=Path, default=None, help="双语 SRT（软字幕）")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    args = ap.parse_args()
    mux(args.video, args.audio, args.out, bilingual_srt=args.subs, ffmpeg=args.ffmpeg)
    print(str(args.out.resolve()))


if __name__ == "__main__":
    main()
