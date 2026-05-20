"""
【模块】video_fetcher.pipeline_runner — subprocess 调用 pipeline/run.py --full。
【调用方】cli pipeline；cwd 为 pipeline 根目录。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from video_fetcher.paths import pipeline_root, resolve_python_executable

logger = logging.getLogger(__name__)


def run_pipeline_full(
    url: str,
    *,
    keep_intermediate: bool = True,
    extra_args: list[str] | None = None,
) -> int:
    """
    在 pipeline 目录下执行: python run.py <url> --full [--keep-intermediate ...]
    返回子进程 exit code。
    """
    pipe = pipeline_root()
    run_py = pipe / "run.py"
    if not run_py.is_file():
        raise FileNotFoundError(f"未找到 {run_py}")

    py = resolve_python_executable(pipe.parent)
    cmd = [py, str(run_py), url.strip(), "--full"]
    if keep_intermediate:
        cmd.append("--keep-intermediate")
    if extra_args:
        cmd.extend(extra_args)

    logger.info("启动译配: %s", " ".join(cmd))
    logger.info("cwd=%s", pipe)
    proc = subprocess.run(cmd, cwd=str(pipe))
    return int(proc.returncode)


def run_pipeline_for_video_id(
    video_id: str,
    *,
    keep_intermediate: bool = True,
    extra_args: list[str] | None = None,
) -> int:
    """对已下载视频用 watch URL 触发 run.py。"""
    url = f"https://www.youtube.com/watch?v={video_id}"
    return run_pipeline_full(
        url,
        keep_intermediate=keep_intermediate,
        extra_args=extra_args,
    )
