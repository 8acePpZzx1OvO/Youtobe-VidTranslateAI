"""
微信视频号发布适配器。

个人号几乎无稳定公开上传 API；企业/机构号请使用微信开放平台能力。
当前默认 dry-run；非 dry-run 时若无 access_token 则返回明确错误。
浏览器自动化（Playwright）不在此实现，见 docs/ARCHITECTURE.md。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from content_hub.publish.base import PublishResult

logger = logging.getLogger(__name__)

PLATFORM_ID = "weixin_channels"


def _dry_run() -> bool:
    return os.environ.get("CONTENT_HUB_PUBLISH_DRY_RUN", "1").strip() not in (
        "0",
        "false",
        "False",
    )


class WeixinChannelsPublisher:
    platform_id = PLATFORM_ID

    def __init__(self, platform_cfg: dict[str, Any] | None = None) -> None:
        self._cfg = platform_cfg or {}
        cred = self._cfg.get("credentials") or {}
        self._app_id = os.environ.get(cred.get("app_id", "WEIXIN_CHANNELS_APP_ID"), "")
        self._app_secret = os.environ.get(
            cred.get("app_secret", "WEIXIN_CHANNELS_APP_SECRET"), ""
        )
        self._token = os.environ.get(
            cred.get("access_token", "WEIXIN_CHANNELS_ACCESS_TOKEN"), ""
        )

    def check_credentials(self) -> tuple[bool, str]:
        if _dry_run():
            return True, "dry_run"
        if self._token or (self._app_id and self._app_secret):
            return True, "credentials_present"
        return (
            False,
            "missing WEIXIN_CHANNELS_ACCESS_TOKEN or APP_ID/APP_SECRET",
        )

    def publish(
        self,
        publish_dir: Path,
        manifest: dict[str, Any],
        rules: dict[str, Any],
    ) -> PublishResult:
        ok, msg = self.check_credentials()
        video = publish_dir / "video.mp4"
        if not video.is_file() and not video.is_symlink():
            return PublishResult(
                platform=PLATFORM_ID,
                success=False,
                message=f"video missing: {video}",
            )

        wx_rules = rules.get("weixin_channels") or {}
        topics = wx_rules.get("topics") or []

        if _dry_run():
            logger.info(
                "[dry-run] weixin_channels upload %s title=%r topics=%s",
                publish_dir.name,
                manifest.get("title"),
                topics,
            )
            return PublishResult(
                platform=PLATFORM_ID,
                success=True,
                dry_run=True,
                message="dry_run_ok",
            )

        if not ok:
            return PublishResult(
                platform=PLATFORM_ID,
                success=False,
                message=msg,
            )

        raise NotImplementedError(
            "微信视频号实际上传未实现：请使用企业微信/开放平台接口，"
            "或参阅 content_hub/docs/ARCHITECTURE.md 中的限制说明。"
        )
