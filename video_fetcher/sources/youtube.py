"""
【模块】video_fetcher.sources.youtube — YouTube 单条/播放列表/频道 URL 解析与下载。
【调用方】cli fetch/batch；内部复用 pipeline/scripts/download.download()。
"""

from __future__ import annotations

import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from video_fetcher.paths import has_raw_download, pipeline_root
from video_fetcher.sources.base import FetchJob, FetchResult

logger = logging.getLogger(__name__)

_YT_HOSTS = (
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
)
_PLAYLIST_HINT = re.compile(r"list=", re.I)
_CHANNEL_HINT = re.compile(
    r"(youtube\.com/(channel/|@c/|@|user/)|youtube\.com/playlist\?)",
    re.I,
)


def _import_pipeline_download():
    """VideoLingo 桥接 download（pipeline/bridge/download_api.py）。"""
    pipe = pipeline_root()
    pipe_s = str(pipe.resolve())
    if pipe_s not in sys.path:
        sys.path.insert(0, pipe_s)
    from bridge.download_api import download  # noqa: WPS433

    return download


def is_youtube_url(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return False
    host = (urlparse(u).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _YT_HOSTS)


def extract_video_id(url: str) -> str | None:
    """从 watch / youtu.be 链接解析视频 ID；播放列表 URL 取 v= 参数。"""
    u = (url or "").strip()
    if not u:
        return None
    parsed = urlparse(u)
    host = (parsed.hostname or "").lower()
    if "youtu.be" in host:
        vid = parsed.path.strip("/").split("/")[0]
        return vid or None
    if "youtube" in host:
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            return qs["v"][0]
        # /shorts/ID、/embed/ID
        parts = [p for p in parsed.path.split("/") if p]
        for key in ("shorts", "embed", "live"):
            if key in parts:
                i = parts.index(key)
                if i + 1 < len(parts):
                    return parts[i + 1]
    return None


def normalize_channel_videos_url(url: str) -> str:
    """
    频道 /@handle 链接规范为「视频」标签页，便于 yt-dlp 按上传顺序取最近 N 条。
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return u
    if not is_youtube_url(u):
        return u
    lower = u.lower()
    if any(
        lower.endswith(suffix)
        for suffix in ("/videos", "/shorts", "/streams", "/playlists", "/featured")
    ):
        return u
    if "/@" in u or "/channel/" in lower or "/user/" in lower or "/c/" in lower:
        return f"{u}/videos"
    return u


def _needs_flat_expand(url: str) -> bool:
    if _PLAYLIST_HINT.search(url):
        return True
    if _CHANNEL_HINT.search(url):
        return True
    if "/@" in url and extract_video_id(url) is None:
        return True
    return False


def expand_youtube(target: str, *, limit: int | None = None) -> list[FetchJob]:
    """
    展开为单条视频任务列表。
    target 可为：单条 URL、播放列表/频道 URL、或本地 urls.txt（每行一个 URL）。
    limit：频道/播放列表仅取前 N 条（按 yt-dlp 列表顺序，一般为最新在前）。
    """
    target = target.strip()
    path = Path(target)
    if path.is_file():
        lines = [
            ln.strip()
            for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        jobs: list[FetchJob] = []
        for line in lines:
            jobs.extend(expand_youtube(line, limit=limit))
        if limit is not None and limit > 0:
            return jobs[: int(limit)]
        return jobs

    if not is_youtube_url(target):
        raise ValueError(f"非 YouTube URL: {target}")

    if not _needs_flat_expand(target):
        vid = extract_video_id(target)
        return [FetchJob(url=target, video_id=vid, source="youtube")]

    target = normalize_channel_videos_url(target)

    try:
        import yt_dlp
    except ImportError as e:
        raise ImportError("请先安装 yt-dlp: pip install yt-dlp") from e

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    if limit is not None and limit > 0:
        opts["playlistend"] = int(limit)
    jobs = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)
    entries = info.get("entries") or [info]
    for ent in entries:
        if not ent:
            continue
        if isinstance(ent, dict):
            eid = ent.get("id") or ent.get("url")
            eurl = ent.get("url") or ent.get("webpage_url") or ""
            title = ent.get("title") or ""
        else:
            continue
        if not eid and not eurl:
            continue
        if eurl and not eurl.startswith("http"):
            eurl = f"https://www.youtube.com/watch?v={eid}"
        if not eurl and eid:
            eurl = f"https://www.youtube.com/watch?v={eid}"
        jobs.append(
            FetchJob(
                url=eurl,
                video_id=str(eid) if eid else extract_video_id(eurl),
                title=title or "",
                source="youtube",
            )
        )
    logger.info("展开 %s → %d 条视频", target[:80], len(jobs))
    return jobs


class YouTubeSource:
    """YouTube 下载源（封装 pipeline download）。"""

    def can_handle(self, url: str) -> bool:
        return is_youtube_url(url)

    def expand(self, target: str) -> list[FetchJob]:
        return expand_youtube(target)

    def fetch(
        self,
        job: FetchJob,
        raw_root: Path,
        *,
        force: bool = False,
        max_retries: int = 3,
    ) -> FetchResult:
        url = job.url
        vid_hint = job.video_id or extract_video_id(url)
        if vid_hint and not force and has_raw_download(raw_root, vid_hint):
            mp4 = raw_root / vid_hint / f"{vid_hint}.mp4"
            vtt = raw_root / vid_hint / f"{vid_hint}.en.vtt"
            return FetchResult(
                video_id=vid_hint,
                video_path=str(mp4),
                subtitle_path=str(vtt) if vtt.is_file() else None,
                title=job.title,
                duration=0,
                url=url,
                skipped=True,
            )

        download_fn = _import_pipeline_download()
        last_err: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                meta = download_fn(url, raw_root, overwrite=force)
                vid = meta.get("video_id") or vid_hint or ""
                return FetchResult(
                    video_id=vid,
                    video_path=meta.get("video_path") or "",
                    subtitle_path=meta.get("subtitle_path"),
                    title=meta.get("title") or job.title,
                    duration=meta.get("duration") or 0,
                    url=url,
                    skipped=False,
                )
            except Exception as e:
                last_err = e
                logger.warning(
                    "下载失败 (%s) 第 %d/%d 次: %s",
                    vid_hint or url[:40],
                    attempt,
                    max_retries,
                    e,
                )
                if attempt < max_retries:
                    time.sleep(min(2.0 * attempt, 30.0))
        assert last_err is not None
        raise last_err


def get_source_for_url(url: str) -> YouTubeSource:
    if is_youtube_url(url):
        return YouTubeSource()
    raise ValueError(f"暂不支持该 URL 源: {url}")
