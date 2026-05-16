#!/usr/bin/env python3
"""
【模块】download.py — YouTube 视频与英文字幕（VTT）下载，供 run.py 第一步使用。
【调用方】命令行独立调试；run.py 内 import download()。

使用 yt-dlp 下载 YouTube 视频与英文字幕（VTT）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("请先安装依赖: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


def _ensure_ffmpeg_on_path() -> None:
    from youtobe_layout import ensure_ffmpeg_on_path

    ensure_ffmpeg_on_path()


def _default_format_selector(max_h: int) -> str:
    """
    优先「最高画质视频轨 + 最佳音轨」再 remux 为 mp4，避免把
    `best[height<=1080]` 放最前时误选 YouTube 渐进式低清（如 format 18）。
    音轨优先 m4a/AAC（mp4a），减少合并后出现 Opus 导致 Windows 自带播放器无声。
    """
    h = max(144, min(int(max_h), 4320))
    return (
        f"bestvideo[height<={h}]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={h}]+bestaudio[acodec^=mp4a]/"
        f"bestvideo[height<={h}]+bestaudio/"
        f"bestvideo[ext=mp4][height<={h}]+bestaudio[ext=m4a]/"
        f"best[height<={h}]/best"
    )


def _ffprobe_audio_codec0(path: Path, ffprobe: str) -> str | None:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return None
    line = (p.stdout or "").strip().splitlines()
    return (line[-1].strip() if line else None) or None


def _maybe_reencode_mp4_audio_to_aac(path: Path) -> None:
    """
    若主音轨为 Opus/Vorbis 等，重编码为 AAC 再写回原路径（视频轨 copy）。
    避免「原片在相册/电影和电视中无声」误以为下载坏了。
    """
    if path.suffix.lower() != ".mp4" or not path.is_file():
        return
    if os.getenv("YOUTOBE_YTDLP_SKIP_AAC_REMUX", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return
    ff = shutil.which("ffmpeg")
    fp = shutil.which("ffprobe")
    if not ff or not fp:
        return
    codec = _ffprobe_audio_codec0(path, fp)
    if not codec:
        return
    c = codec.strip().lower()
    if c in ("aac", "mp3"):
        return
    tmp = path.with_name(f"{path.stem}.audio-aac.tmp{path.suffix}")
    cmd = [
        ff,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 4096:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        print(
            f"警告: 音轨 {codec} → AAC 自动转封装失败，保留原文件。"
            f" 详情: {(p.stderr or '')[:500]}",
            file=sys.stderr,
        )
        return
    os.replace(str(tmp), str(path))
    print(
        f"提示: 已将原片主音轨由 {codec} 转为 AAC（视频轨未重编码），便于系统播放器与后续 mux。",
        file=sys.stderr,
    )


def _progress_hook(d: dict) -> None:
    if d.get("status") == "downloading":
        pct = d.get("_percent_str", "")
        spd = d.get("_speed_str", "")
        eta = d.get("_eta_str", "")
        sys.stdout.write(f"\r下载中 {pct} {spd} ETA {eta}   ")
        sys.stdout.flush()
    elif d.get("status") == "finished":
        sys.stdout.write("\n")
        sys.stdout.flush()


def download(
    url: str,
    out_dir: Path,
    *,
    overwrite: bool = False,
) -> dict:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass

    _ensure_ffmpeg_on_path()

    out_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = (out_dir / "%(id)s" / "%(id)s.%(ext)s").as_posix()
    try:
        max_h = int((os.getenv("YOUTOBE_YTDLP_MAX_HEIGHT", "1080").strip() or "1080"))
    except ValueError:
        max_h = 1080
    fmt_env = (os.getenv("YOUTOBE_YTDLP_FORMAT", "") or "").strip()
    fmt = fmt_env if fmt_env else _default_format_selector(max_h)

    opts: dict = {
        "format": fmt,
        "merge_output_format": "mp4",
        "overwrites": overwrite
        or os.getenv("YOUTOBE_YTDLP_OVERWRITE", "").strip().lower()
        in ("1", "true", "yes", "on"),
        "outtmpl": outtmpl,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "subtitlesformat": "vtt",
        "writethumbnail": False,
        "quiet": False,
        "no_warnings": False,
        "progress_hooks": [_progress_hook],
        # 缓解 CDN 偶发 SSL 断连、半开连接被重置
        "retries": int(os.getenv("YOUTOBE_YTDLP_RETRIES", "20")),
        "fragment_retries": int(os.getenv("YOUTOBE_YTDLP_FRAGMENT_RETRIES", "20")),
        "socket_timeout": float(os.getenv("YOUTOBE_YTDLP_SOCKET_TIMEOUT", "90")),
        "sleep_interval_requests": float(os.getenv("YOUTOBE_YTDLP_SLEEP_REQUESTS", "0.5")),
        "sleep_interval_subtitles": float(os.getenv("YOUTOBE_YTDLP_SLEEP_SUBS", "0")),
        # 分片串行可降低 TLS 握手失败概率（略慢但更稳）
        "concurrent_fragment_downloads": int(
            os.getenv("YOUTOBE_YTDLP_CONCURRENT_FRAGMENTS", "1")
        ),
    }
    # 不显式覆盖 player_client：强制 web/ios/android 易触发 EJS / PO Token，反只剩渐进式 360p（format 18）。
    # 需要指定客户端时用环境变量 YOUTOBE_YTDLP_EXTRACTOR_ARGS（JSON）扩展，例如:
    #   {"youtube": {"player_client": ["android_vr", "web"]}}
    ext_json = (os.getenv("YOUTOBE_YTDLP_EXTRACTOR_ARGS", "") or "").strip()
    if ext_json:
        try:
            opts["extractor_args"] = json.loads(ext_json)
        except json.JSONDecodeError as e:
            print(f"警告: YOUTOBE_YTDLP_EXTRACTOR_ARGS 不是合法 JSON，已忽略: {e}", file=sys.stderr)

    proxy = (
        os.getenv("YOUTOBE_YTDLP_PROXY", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("https_proxy", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
        or os.getenv("http_proxy", "").strip()
    )
    if proxy:
        opts["proxy"] = proxy
        print(f"使用代理下载: {proxy[:40]}…" if len(proxy) > 40 else f"使用代理下载: {proxy}", file=sys.stderr)

    if os.getenv("YOUTOBE_YTDLP_INSECURE_SSL", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        opts["nocheckcertificate"] = True
        print(
            "警告: 已启用 YOUTOBE_YTDLP_INSECURE_SSL（不校验 HTTPS 证书），存在中间人风险。",
            file=sys.stderr,
        )
    if not shutil.which("ffmpeg"):
        print(
            "警告: 未在 PATH 中找到 ffmpeg，可能无法合并高清音视频轨；"
            "请安装 ffmpeg 或 pip install static-ffmpeg（见 requirements-pro.txt）。",
            file=sys.stderr,
        )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_path = Path(ydl.prepare_filename(info))
    try:
        _maybe_reencode_mp4_audio_to_aac(video_path)
    except OSError as e:
        print(f"警告: 音轨 AAC 规范化跳过: {e}", file=sys.stderr)
    vid = info.get("id", video_path.stem)
    sub = out_dir / vid / f"{vid}.en.vtt"
    if not sub.exists():
        sub = video_path.with_suffix(".en.vtt")
    if not sub.exists():
        vid_d = out_dir / vid
        sub = next(vid_d.glob(f"{vid}*.en.vtt"), None) if vid_d.is_dir() else None
    if sub is None or not Path(sub).exists():
        sub = next(out_dir.glob(f"{vid}*.en.vtt"), None)
    return {
        "video_path": str(video_path),
        "subtitle_path": str(sub) if sub is not None and Path(sub).exists() else None,
        "title": info.get("title") or "",
        "duration": info.get("duration") or 0,
        "video_id": vid,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="下载 YouTube 视频与英文字幕")
    p.add_argument("url", help="YouTube 链接")
    p.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("output") / "raw",
        help="输出根目录（默认 output/raw；每个视频写入 <id>/<id>.mp4）",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="已存在同路径 mp4 时仍重新下载（等价于设 YOUTOBE_YTDLP_OVERWRITE=1）",
    )
    args = p.parse_args()
    res = download(args.url, args.output_dir, overwrite=args.overwrite)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
