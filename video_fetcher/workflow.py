"""
【模块】video_fetcher.workflow — 全流程：YouTube 拉取 → pipeline 译配 → 搬运目录精简。
【调用方】cli 子命令 workflow。

成品约定（与用户需求一致）：
  output/raw/<id>/           仅 <id>.mp4
  output/processed/<id>/     仅 <id>.bilingual.srt + <id>_zh_dub_hard_bilingual.mp4
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from video_fetcher.batch import (
    STATUS_DONE,
    STATUS_SKIPPED,
    default_state_path,
    run_batch_download,
)
from video_fetcher.paths import output_processed_root, output_raw_root, pipeline_root
from video_fetcher.relocate_outputs import apply_relocate_layout
from video_fetcher.sources.youtube import (
    extract_video_id,
    is_youtube_url,
    normalize_channel_videos_url,
)
from video_fetcher.pipeline_runner import run_pipeline_for_video_id, run_pipeline_full

logger = logging.getLogger(__name__)


def run_workflow_single(
    url: str,
    *,
    raw_root: Path | None = None,
    proc_root: Path | None = None,
) -> tuple[int, dict]:
    """单条 URL：run.py --full（保留中间产物）→ 归档为 raw 仅 mp4、processed 双语+硬烧。"""
    pipe = pipeline_root()
    raw = raw_root or output_raw_root(pipe)
    proc = proc_root or output_processed_root(pipe)

    rc = run_pipeline_full(url, keep_intermediate=True)
    vid = extract_video_id(url)
    if rc != 0:
        return rc, {"url": url, "video_id": vid, "pipeline_rc": rc}

    if not vid:
        logger.error("无法解析 video_id: %s", url)
        return 1, {"url": url, "error": "no video_id"}

    layout = apply_relocate_layout(raw, proc, vid)
    return 0, {"video_id": vid, "url": url, "outputs": layout}


def run_workflow_batch(
    target: str,
    *,
    raw_root: Path | None = None,
    proc_root: Path | None = None,
    state_path: Path | None = None,
    resume: bool = False,
    max_workers: int = 1,
    skip_existing: bool = True,
    force: bool = False,
    max_retries: int = 3,
    limit: int | None = None,
) -> tuple[int, list[dict]]:
    """批量：下载队列 → 逐条译配 → 归档。"""
    pipe = pipeline_root()
    raw = raw_root or output_raw_root(pipe)
    proc = proc_root or output_processed_root(pipe)
    state_file = state_path or default_state_path(target)

    if limit and not Path(target).is_file() and is_youtube_url(target):
        target = normalize_channel_videos_url(target)

    state, dl_code = run_batch_download(
        target,
        state_path=state_file,
        raw_root=raw,
        resume=resume,
        max_workers=max_workers,
        skip_existing=skip_existing,
        force=force,
        max_retries=max_retries,
        limit=limit,
    )

    summaries: list[dict] = []
    exit_code = dl_code
    items = [
        it
        for it in state.items
        if it.status in (STATUS_DONE, STATUS_SKIPPED) and it.video_id
    ]

    for it in items:
        vid = it.video_id
        assert vid
        rc = run_pipeline_for_video_id(vid, keep_intermediate=True)
        if rc != 0:
            exit_code = rc
            summaries.append(
                {"video_id": vid, "url": it.url, "pipeline_rc": rc, "error": "pipeline failed"}
            )
            continue
        layout = apply_relocate_layout(raw, proc, vid)
        summaries.append({"video_id": vid, "url": it.url, "outputs": layout})

    return exit_code, summaries


def run_workflow(
    target: str,
    *,
    batch_mode: bool = False,
    limit: int | None = None,
    raw_root: Path | None = None,
    proc_root: Path | None = None,
    state_path: Path | None = None,
    resume: bool = False,
    max_workers: int = 1,
    skip_existing: bool = True,
    force: bool = False,
    max_retries: int = 3,
) -> tuple[int, object]:
    """统一入口：单 URL 或 batch/频道/列表文件。"""
    target = target.strip()
    path = Path(target)
    is_batch = (
        batch_mode
        or path.is_file()
        or _looks_like_playlist_or_channel(target)
    )

    if is_batch:
        code, summaries = run_workflow_batch(
            target,
            limit=limit,
            raw_root=raw_root,
            proc_root=proc_root,
            state_path=state_path,
            resume=resume,
            max_workers=max_workers,
            skip_existing=skip_existing,
            force=force,
            max_retries=max_retries,
        )
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return code, summaries

    if not is_youtube_url(target):
        raise ValueError(f"需要 YouTube URL: {target}")
    code, summary = run_workflow_single(
        target,
        raw_root=raw_root,
        proc_root=proc_root,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return code, summary


def _looks_like_playlist_or_channel(target: str) -> bool:
    t = target.lower()
    if "list=" in t or "/playlist" in t:
        return True
    if "/@" in t and extract_video_id(target) is None:
        return True
    if target.startswith("@"):
        return True
    return False
