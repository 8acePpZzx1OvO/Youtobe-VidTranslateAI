#!/usr/bin/env python3
"""
【模块】apply_video_playback_speed.py — 成片或 raw MP4 的整片 setpts+atempo 倍速导出（一般不改动 SRT）。
【调用方】命令行；run.py 在配置 YOUTOBE_VIDEO_SPEED 时用于导出倍速观看版。

整片「观看倍速」工具，分两种模式：

1) **raw**（默认）：就地替换 raw 下 MP4，并可选缩放 SRT 时间轴（旧行为，一般不推荐与 run.py 联用）。

2) **rendered**：对已成片 MP4（硬烧字幕在画面内，或仅含视频+配音音轨）整体 setpts+atempo，
   **不修改任何 SRT**。用于在 1.0× 译配完成后，再额外导出一份倍速观看版。

倍速含义：1.5 表示 1.5 倍速，片长约为原来的 1/1.5。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import pysrt
except ImportError:
    print("请先安装: pip install pysrt", file=sys.stderr)
    sys.exit(1)


def _has_audio_stream(video: Path, ffprobe: str) -> bool:
    r = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
    )
    return bool(r.stdout.strip())


def _atempo_filter_chain(speed: float) -> str:
    """生成 ffmpeg atempo 链（每段在约 0.5–2.0 之间）。"""
    parts: list[str] = []
    s = float(speed)
    if s > 1.0:
        while s > 2.0 + 1e-9:
            parts.append("atempo=2.0")
            s /= 2.0
        parts.append(_fmt_atempo(s))
    elif s < 1.0:
        while s < 0.5 - 1e-9:
            parts.append("atempo=0.5")
            s /= 0.5
        parts.append(_fmt_atempo(max(0.5, min(s, 2.0))))
    else:
        parts.append("atempo=1.0")
    return ",".join(parts)


def _fmt_atempo(x: float) -> str:
    t = f"{x:.6f}".rstrip("0").rstrip(".")
    return f"atempo={t}" if t else "atempo=1.0"


def _ffmpeg_playback_speed(
    src: Path,
    dst: Path,
    speed: float,
    *,
    ffmpeg: str = "ffmpeg",
    strip_subtitles: bool = False,
) -> None:
    """用 setpts + atempo 生成变速视频（需重新编码视频轨）。"""
    if speed <= 0:
        raise ValueError("speed 必须为正数")
    fp = shutil.which("ffprobe") or os.getenv("FFPROBE", "ffprobe").strip() or "ffprobe"
    has_a = _has_audio_stream(src, fp)
    vf = f"setpts=PTS/{speed}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp.mp4")
    cmd: list[str | Path] = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        str(src),
        "-filter:v",
        vf,
    ]
    if has_a:
        af = _atempo_filter_chain(speed)
        cmd.extend(
            [
                "-filter:a",
                af,
                "-c:v",
                "libx264",
                "-preset",
                os.getenv("YOUTOBE_SPEED_ENCODE_PRESET", "fast").strip() or "fast",
                "-crf",
                os.getenv("YOUTOBE_SPEED_ENCODE_CRF", "23").strip() or "23",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
            ]
        )
    else:
        cmd.extend(
            [
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                os.getenv("YOUTOBE_SPEED_ENCODE_PRESET", "fast").strip() or "fast",
                "-crf",
                os.getenv("YOUTOBE_SPEED_ENCODE_CRF", "23").strip() or "23",
                "-movflags",
                "+faststart",
            ]
        )
    if strip_subtitles:
        cmd.append("-sn")
    cmd.append(str(tmp))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg 变速失败")
    tmp.replace(dst)


def speed_rendered_mp4(
    src: Path,
    dst: Path,
    speed: float,
    *,
    ffmpeg: str = "ffmpeg",
) -> None:
    """
    对已成片 MP4 整体变速（-filter:v / -filter:a），不碰磁盘上的 SRT。
    输出使用 -sn 不写字幕轨；硬烧成片字幕已在画面像素内不受影响。
    """
    if abs(speed - 1.0) < 1e-9:
        shutil.copy2(src, dst)
        return
    _ffmpeg_playback_speed(
        src, dst, speed, ffmpeg=ffmpeg, strip_subtitles=True
    )


def scale_srt_to_speed(srt_path: Path, speed: float, *, encoding: str = "utf-8") -> None:
    """将每条字幕起止时间除以 speed（倍速越快，时间轴越压缩）。"""
    if abs(speed - 1.0) < 1e-9:
        return
    subs = pysrt.open(str(srt_path), encoding=encoding)
    for sub in subs:
        o0 = int(sub.start.ordinal / speed + 0.5)
        o1 = int(sub.end.ordinal / speed + 0.5)
        if o1 <= o0:
            o1 = o0 + 1
        sub.start = pysrt.SubRipTime(milliseconds=o0)
        sub.end = pysrt.SubRipTime(milliseconds=o1)
    subs.save(str(srt_path), encoding=encoding)


def apply_speed_inplace(
    video: Path,
    speed: float,
    *,
    scale_srt_paths: list[Path] | None = None,
    ffmpeg: str = "ffmpeg",
) -> None:
    """
    就地替换 video 为变速版本；首次会备份为 <stem>.original.mp4。
    若提供 scale_srt_paths，在变速后按比例改写这些 SRT（通常为 en.srt）。
    """
    if abs(speed - 1.0) < 1e-9:
        return
    if not video.exists():
        raise FileNotFoundError(video)
    orig = video.with_name(f"{video.stem}.original.mp4")
    if orig.exists():
        src = orig
    else:
        shutil.copy2(video, orig)
        src = orig
    tmp_out = video.with_suffix(".speedwork.mp4")
    _ffmpeg_playback_speed(src, tmp_out, speed, ffmpeg=ffmpeg, strip_subtitles=False)
    tmp_out.replace(video)
    if scale_srt_paths:
        for p in scale_srt_paths:
            if p.exists():
                scale_srt_to_speed(p, speed)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="整片倍速：raw 就地改视频+可选缩放 SRT；rendered 仅对成片再导出一份"
    )
    ap.add_argument(
        "--mode",
        choices=("raw", "rendered"),
        default="raw",
        help="raw=改下载视频并可缩放 SRT；rendered=仅对成片 MP4 导出倍速副本（推荐与 run.py --video-speed 配合）",
    )
    ap.add_argument(
        "--video",
        type=Path,
        default=None,
        help="raw 模式：要就地覆盖的 MP4",
    )
    ap.add_argument(
        "--input",
        type=Path,
        default=None,
        help="rendered 模式：源成片（如 *_zh_dub_hard_bilingual.mp4）",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="rendered 模式：输出路径（勿与 input 相同）",
    )
    ap.add_argument(
        "--speed",
        type=float,
        required=True,
        help="播放倍速（1.0 原速；1.25 更快；0.8 更慢）。建议 0.5–2.0",
    )
    ap.add_argument(
        "--scale-srt",
        type=Path,
        nargs="*",
        default=(),
        help="仅 raw 模式：变速后按比例缩放的 SRT",
    )
    ap.add_argument("--ffmpeg", default="ffmpeg")
    args = ap.parse_args()
    s = float(args.speed)
    if not (0.25 <= s <= 4.0):
        print("错误: --speed 须在 0.25–4.0 之间", file=sys.stderr)
        sys.exit(2)
    if abs(s - 1.0) < 1e-9:
        print("倍速为 1.0，跳过。", file=sys.stderr)
        return
    if args.mode == "rendered":
        if args.input is None or args.output is None:
            print("错误: rendered 模式需要 --input 与 --output", file=sys.stderr)
            sys.exit(2)
        if args.input.resolve() == args.output.resolve():
            print("错误: --input 与 --output 不能为同一路径", file=sys.stderr)
            sys.exit(2)
        speed_rendered_mp4(args.input, args.output, s, ffmpeg=args.ffmpeg)
        print(str(args.output.resolve()))
        return
    if args.video is None:
        print("错误: raw 模式需要 --video", file=sys.stderr)
        sys.exit(2)
    apply_speed_inplace(
        args.video,
        s,
        scale_srt_paths=list(args.scale_srt) if args.scale_srt else None,
        ffmpeg=args.ffmpeg,
    )
    print(str(args.video.resolve()))


if __name__ == "__main__":
    main()
