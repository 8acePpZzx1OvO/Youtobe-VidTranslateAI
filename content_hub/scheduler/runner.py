"""
定时调度占位（Phase 5）。

推荐使用系统任务计划程序调用：
  content-hub run-once --config content_hub/config/sources.yaml

或安装 apscheduler 后在此扩展 daemon 循环。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from content_hub.runner import run_once

logger = logging.getLogger(__name__)


def run_daemon(
    config_path: str | Path,
    *,
    interval_seconds: int = 3600,
    once: bool = False,
) -> int:
    """简易轮询：每隔 interval_seconds 执行一次 run_once。"""
    if once:
        return run_once(config_path)

    logger.info("daemon 启动，间隔 %ds（Ctrl+C 停止）", interval_seconds)
    try:
        while True:
            rc = run_once(config_path)
            logger.info("run_once 结束 rc=%s", rc)
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        logger.info("daemon 已停止")
        return 0
