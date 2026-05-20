"""按 filters.yaml 规则筛选 FetchJob。"""

from __future__ import annotations

import re
from typing import Any

from video_fetcher.sources.base import FetchJob


def _match_keywords(text: str, keywords: list[str], *, mode: str) -> bool:
    if not keywords:
        return True
    lower = (text or "").lower()
    if mode == "include":
        return any(k.lower() in lower for k in keywords)
    return not any(k.lower() in lower for k in keywords)


def _channel_id_from_url(url: str) -> str:
    u = (url or "").lower()
    for pat in (
        r"youtube\.com/channel/([^/?#]+)",
        r"youtube\.com/@([^/?#]+)",
        r"youtube\.com/c/([^/?#]+)",
        r"youtube\.com/user/([^/?#]+)",
    ):
        m = re.search(pat, u)
        if m:
            return m.group(1)
    return ""


def job_passes_filters(
    job: FetchJob,
    filters: dict[str, Any],
    *,
    duration_seconds: float | None = None,
) -> tuple[bool, str]:
    """返回 (通过, 拒绝原因)。"""
    title = job.title or ""
    url = job.url or ""

    title_cfg = filters.get("title") or {}
    if not _match_keywords(title, title_cfg.get("include_keywords") or [], mode="include"):
        return False, "title_missing_include_keyword"
    if not _match_keywords(title, title_cfg.get("exclude_keywords") or [], mode="exclude"):
        return False, "title_excluded_keyword"

    allow = filters.get("channel_allowlist") or []
    block = filters.get("channel_blocklist") or []
    ch = _channel_id_from_url(url)
    if allow:
        if ch and ch not in allow and not any(a in url for a in allow):
            return False, "channel_not_in_allowlist"
    if block and ch and ch in block:
        return False, "channel_blocked"

    dur_cfg = filters.get("duration") or {}
    if duration_seconds is not None:
        mn = dur_cfg.get("min_seconds")
        mx = dur_cfg.get("max_seconds")
        if mn is not None and duration_seconds < float(mn):
            return False, "too_short"
        if mx is not None and duration_seconds > float(mx):
            return False, "too_long"

    if filters.get("require_english_subtitle"):
        meta = getattr(job, "metadata", None) or {}
        if not meta.get("has_english_subtitle"):
            return False, "no_english_subtitle"

    return True, ""
