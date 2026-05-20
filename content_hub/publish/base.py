"""发布适配器抽象。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class PublishResult:
    platform: str
    success: bool
    dry_run: bool = False
    remote_id: str | None = None
    message: str = ""
    extra: dict[str, Any] | None = None


class PublisherAdapter(Protocol):
    platform_id: str

    def check_credentials(self) -> tuple[bool, str]: ...

    def publish(
        self,
        publish_dir: Path,
        manifest: dict[str, Any],
        rules: dict[str, Any],
    ) -> PublishResult: ...
