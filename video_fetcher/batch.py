"""
【模块】video_fetcher.batch — 批量下载队列、batch_state.json 持久化与并发。
【调用方】cli 子命令 batch / pipeline batch。
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from video_fetcher.paths import has_raw_download, output_raw_root, pipeline_root
from video_fetcher.sources.base import FetchJob, FetchResult
from video_fetcher.sources.youtube import YouTubeSource, expand_youtube

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_DOWNLOADING = "downloading"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


@dataclass
class BatchItem:
    url: str
    video_id: str | None = None
    title: str = ""
    status: str = STATUS_PENDING
    error: str | None = None
    attempts: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> BatchItem:
        return cls(
            url=d.get("url", ""),
            video_id=d.get("video_id"),
            title=d.get("title", ""),
            status=d.get("status", STATUS_PENDING),
            error=d.get("error"),
            attempts=int(d.get("attempts") or 0),
            metadata=dict(d.get("metadata") or {}),
        )


@dataclass
class BatchState:
    source: str
    created_at: str
    updated_at: str
    items: list[BatchItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "items": [it.to_dict() for it in self.items],
        }

    @classmethod
    def from_dict(cls, d: dict) -> BatchState:
        return cls(
            source=d.get("source", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            items=[BatchItem.from_dict(x) for x in d.get("items") or []],
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_state_path(target: str) -> Path:
    p = Path(target)
    if p.is_file():
        return p.parent / "batch_state.json"
    return Path.cwd() / "batch_state.json"


def load_state(path: Path) -> BatchState | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return BatchState.from_dict(data)


def save_state(path: Path, state: BatchState) -> None:
    state.updated_at = _utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_state_from_target(target: str, *, limit: int | None = None) -> BatchState:
    jobs = expand_youtube(target, limit=limit)
    now = _utc_now()
    items = [
        BatchItem(
            url=j.url,
            video_id=j.video_id,
            title=j.title,
            status=STATUS_PENDING,
        )
        for j in jobs
    ]
    return BatchState(source=target, created_at=now, updated_at=now, items=items)


def merge_resume(existing: BatchState, fresh: BatchState) -> BatchState:
    """resume：保留已有条目的 status/error/metadata，仅追加新 URL。"""
    by_url = {it.url: it for it in existing.items}
    merged: list[BatchItem] = []
    for it in fresh.items:
        if it.url in by_url:
            merged.append(by_url[it.url])
        else:
            merged.append(it)
    for url, it in by_url.items():
        if url not in {x.url for x in fresh.items}:
            merged.append(it)
    return BatchState(
        source=fresh.source or existing.source,
        created_at=existing.created_at or fresh.created_at,
        updated_at=_utc_now(),
        items=merged,
    )


def _item_to_job(item: BatchItem) -> FetchJob:
    return FetchJob(
        url=item.url,
        video_id=item.video_id,
        title=item.title,
        source="youtube",
    )


def _apply_result(item: BatchItem, res: FetchResult) -> None:
    item.video_id = res.video_id
    item.title = res.title or item.title
    item.metadata = res.to_dict()
    item.error = None
    item.status = STATUS_SKIPPED if res.skipped else STATUS_DONE


def run_batch_download(
    target: str,
    *,
    state_path: Path | None = None,
    raw_root: Path | None = None,
    resume: bool = False,
    max_workers: int = 1,
    skip_existing: bool = True,
    force: bool = False,
    max_retries: int = 3,
    limit: int | None = None,
    on_item_done: Callable[[BatchItem], None] | None = None,
) -> tuple[BatchState, int]:
    """
    执行批量下载。返回 (state, exit_code)；exit_code 0 全成功，1 有失败。
    """
    state_file = state_path or default_state_path(target)
    raw = raw_root or output_raw_root(pipeline_root())
    source = YouTubeSource()

    fresh = build_state_from_target(target, limit=limit)
    old = load_state(state_file) if resume else None
    if old and resume:
        state = merge_resume(old, fresh)
        logger.info("续跑 batch，共 %d 条（自 %s）", len(state.items), state_file)
    else:
        state = fresh
        logger.info("新建 batch，共 %d 条", len(state.items))

    save_state(state_file, state)
    lock = threading.Lock()

    def work(idx: int, item: BatchItem) -> None:
        if item.status in (STATUS_DONE, STATUS_SKIPPED) and resume and not force:
            return
        vid = item.video_id
        if (
            skip_existing
            and not force
            and vid
            and has_raw_download(raw, vid)
        ):
            with lock:
                item.status = STATUS_SKIPPED
                item.error = None
                save_state(state_file, state)
            if on_item_done:
                on_item_done(item)
            return

        with lock:
            item.status = STATUS_DOWNLOADING
            item.attempts += 1
            save_state(state_file, state)

        job = _item_to_job(item)
        try:
            res = source.fetch(
                job,
                raw,
                force=force,
                max_retries=max_retries,
            )
            with lock:
                _apply_result(item, res)
                save_state(state_file, state)
            if on_item_done:
                on_item_done(item)
        except Exception as e:
            with lock:
                item.status = STATUS_FAILED
                item.error = str(e)
                save_state(state_file, state)
            logger.error("条目失败 %s: %s", item.url, e)

    pending_indices = [
        i
        for i, it in enumerate(state.items)
        if it.status not in (STATUS_DONE, STATUS_SKIPPED) or force
    ]
    if not pending_indices and resume:
        logger.info("无待处理条目")
        failed = sum(1 for it in state.items if it.status == STATUS_FAILED)
        return state, 1 if failed else 0

    workers = max(1, int(max_workers))
    if workers == 1:
        for i in pending_indices:
            work(i, state.items[i])
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(work, i, state.items[i]) for i in pending_indices]
            for _ in as_completed(futs):
                pass

    save_state(state_file, state)
    failed = sum(1 for it in state.items if it.status == STATUS_FAILED)
    done = sum(1 for it in state.items if it.status in (STATUS_DONE, STATUS_SKIPPED))
    logger.info("batch 结束: 成功/跳过 %d, 失败 %d", done, failed)
    return state, (1 if failed else 0)
