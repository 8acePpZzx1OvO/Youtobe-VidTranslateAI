"""
content_hub CLI：发现 → 译配 → 国内平台发布。

  content-hub run-once --config content_hub/config/sources.yaml
  content-hub discover --config ...
  content-hub publish-ready --config ...
  content-hub daemon --config ... --interval 3600
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from content_hub.paths import content_hub_root
from content_hub.doctor import run_doctor
from content_hub.runner import publish_ready_only, run_once
from content_hub.scheduler.runner import run_daemon


def _default_config() -> Path:
    p = content_hub_root() / "config" / "sources.yaml"
    if p.is_file():
        return p
    return content_hub_root() / "config" / "sources.example.yaml"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="content-hub", description="外文视频搬运分发流")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def add_config(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--config",
            type=Path,
            default=_default_config(),
            help="sources.yaml 路径",
        )

    sp = sub.add_parser(
        "run-once", help="发现 → 译配 → 发布（全自动）", parents=[common]
    )
    add_config(sp)
    sp.add_argument("--limit", type=int, default=None, help="最多处理 N 条")
    sp.add_argument(
        "--skip-pipeline",
        action="store_true",
        help="跳过译配，仅打包已有 processed 并发布",
    )
    sp.add_argument(
        "--skip-publish",
        action="store_true",
        help="只跑到 publish_ready",
    )

    sp = sub.add_parser("discover", help="仅发现并写入台账", parents=[common])
    add_config(sp)
    sp.add_argument("--limit", type=int, default=None)

    sp = sub.add_parser(
        "publish-ready", help="仅发布台账中 publish_ready 任务", parents=[common]
    )
    add_config(sp)

    sp = sub.add_parser("doctor", help="环境自检（配置/代理/可选成片）", parents=[common])
    sp.add_argument("--video-id", type=str, default=None, help="检查 processed 成片是否存在")

    sp = sub.add_parser("daemon", help="按间隔循环 run-once", parents=[common])
    add_config(sp)
    sp.add_argument("--interval", type=int, default=3600, help="秒")
    sp.add_argument("--once", action="store_true", help="只跑一轮后退出")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "verbose", False):
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(levelname)s %(name)s: %(message)s",
        )
    elif args.command != "doctor":
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
        )

    if args.command == "doctor":
        return run_doctor(video_id=getattr(args, "video_id", None))

    cfg = args.config

    if args.command == "run-once":
        return run_once(
            cfg,
            limit=args.limit,
            skip_pipeline=args.skip_pipeline,
            skip_publish=args.skip_publish,
        )
    if args.command == "discover":
        return run_once(cfg, discover_only=True, limit=args.limit)
    if args.command == "publish-ready":
        return publish_ready_only(cfg)
    if args.command == "daemon":
        return run_daemon(cfg, interval_seconds=args.interval, once=args.once)
    return 1


if __name__ == "__main__":
    sys.exit(main())
