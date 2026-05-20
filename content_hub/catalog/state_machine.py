"""任务状态合法迁移。"""

from __future__ import annotations

from content_hub.catalog.models import (
    STATUS_DISCOVERED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_PUBLISH_READY,
    STATUS_PUBLISHED,
    STATUS_PUBLISHING,
    STATUS_QUEUED,
)

_ALLOWED: dict[str, frozenset[str]] = {
    STATUS_DISCOVERED: frozenset({STATUS_QUEUED, STATUS_FAILED}),
    STATUS_QUEUED: frozenset({STATUS_DOWNLOADING, STATUS_PROCESSING, STATUS_FAILED}),
    STATUS_DOWNLOADING: frozenset({STATUS_PROCESSING, STATUS_FAILED}),
    STATUS_PROCESSING: frozenset({STATUS_PUBLISH_READY, STATUS_FAILED}),
    STATUS_PUBLISH_READY: frozenset({STATUS_PUBLISHING, STATUS_FAILED}),
    STATUS_PUBLISHING: frozenset({STATUS_PUBLISHED, STATUS_FAILED}),
    STATUS_FAILED: frozenset(
        {STATUS_QUEUED, STATUS_PROCESSING, STATUS_PUBLISH_READY, STATUS_PUBLISHING}
    ),
    STATUS_PUBLISHED: frozenset(),
}


def can_transition(current: str, new: str) -> bool:
    if current == new:
        return True
    return new in _ALLOWED.get(current, frozenset())


def assert_transition(current: str, new: str) -> None:
    if not can_transition(current, new):
        raise ValueError(f"非法状态迁移: {current} → {new}")
