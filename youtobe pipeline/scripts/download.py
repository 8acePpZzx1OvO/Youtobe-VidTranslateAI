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
import sys
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("请先安装依赖: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


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


def download(url: str, out_dir: Path) -> dict:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass

    out_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = (out_dir / "%(id)s" / "%(id)s.%(ext)s").as_posix()
    opts: dict = {
        # 优先选已合并的单文件，避免在未安装 ffmpeg 时无法合并 DASH 音视频
        "format": (
            "best[height<=1080]/best/"
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "best[height<=1080][ext=mp4]/best"
        ),
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
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            },
        },
    }

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
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_path = Path(ydl.prepare_filename(info))
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
    args = p.parse_args()
    res = download(args.url, args.output_dir)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
