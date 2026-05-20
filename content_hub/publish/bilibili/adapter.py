"""
B站发布适配器。

实际上传需配置 BILIBILI_SESSDATA 等 Cookie，并安装对应 SDK（见 requirements-publish.txt）。
CONTENT_HUB_PUBLISH_DRY_RUN=1（默认）时仅校验并写出台账，不调用上传 API。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from content_hub.publish.base import PublishResult

logger = logging.getLogger(__name__)

PLATFORM_ID = "bilibili"


def _dry_run() -> bool:
    return os.environ.get("CONTENT_HUB_PUBLISH_DRY_RUN", "1").strip() not in (
        "0",
        "false",
        "False",
    )


class BilibiliPublisher:
    platform_id = PLATFORM_ID

    def __init__(self, platform_cfg: dict[str, Any] | None = None) -> None:
        self._cfg = platform_cfg or {}
        cred = self._cfg.get("credentials") or {}
        self._sessdata = os.environ.get(cred.get("sessdata", "BILIBILI_SESSDATA"), "")
        self._bili_jct = os.environ.get(cred.get("bili_jct", "BILIBILI_BILI_JCT"), "")
        self._dedeuserid = os.environ.get(
            cred.get("dedeuserid", "BILIBILI_DEDEUSERID"), ""
        )

    def check_credentials(self) -> tuple[bool, str]:
        if _dry_run():
            return True, "dry_run"
        if self._sessdata and self._bili_jct and self._dedeuserid:
            return True, "cookies_present"
        return False, "missing BILIBILI_SESSDATA / BILIBILI_BILI_JCT / BILIBILI_DEDEUSERID"

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

        bili_rules = rules.get("bilibili") or {}
        tid = bili_rules.get("tid", 171)

        if _dry_run():
            logger.info(
                "[dry-run] bilibili upload %s title=%r tid=%s",
                publish_dir.name,
                manifest.get("title"),
                tid,
            )
            return PublishResult(
                platform=PLATFORM_ID,
                success=True,
                dry_run=True,
                message="dry_run_ok",
                extra={"tid": tid},
            )

        if not ok:
            return PublishResult(
                platform=PLATFORM_ID,
                success=False,
                message=msg,
            )

        # 实际上传：接入 bilibili 开放 API / 创作中心上传接口（需自行安装 SDK）
        try:
            return self._upload_real(publish_dir, manifest, bili_rules)
        except NotImplementedError:
            raise
        except Exception as e:
            logger.exception("bilibili upload failed")
            return PublishResult(
                platform=PLATFORM_ID,
                success=False,
                message=str(e),
            )

    def _upload_real(
        self,
        publish_dir: Path,
        manifest: dict[str, Any],
        bili_rules: dict[str, Any],
    ) -> PublishResult:
        """
        实际上传占位：安装 bilibili-api-python 或自研 HTTP 分片上传后在此实现。
        文档：https://open.bilibili.com/
        """
        _ = publish_dir, manifest, bili_rules
        raise NotImplementedError(
            "B站实际上传未配置：请设置 CONTENT_HUB_PUBLISH_DRY_RUN=0 并实现 "
            "BilibiliPublisher._upload_real，或安装 bilibili-api-python"
        )
