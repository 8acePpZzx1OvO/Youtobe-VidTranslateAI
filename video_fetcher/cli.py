"""
【模块】video_fetcher.cli — 命令行：fetch / batch / pipeline。
【调用方】python -m video_fetcher 或 console_scripts video-fetcher。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from video_fetcher.batch import (
    STATUS_DONE,
    STATUS_SKIPPED,
    default_state_path,
    run_batch_download,
)
from video_fetcher.paths import (
    find_repo_root,
    output_processed_root,
    output_raw_root,
    pipeline_root,
)
from video_fetcher.sources.base import FetchJob
from video_fetcher.sources.youtube import YouTubeSource, is_youtube_url
from video_fetcher.relocate_outputs import apply_relocate_layout
from video_fetcher.workflow import run_workflow
from video_fetcher.pipeline_runner import run_pipeline_full, run_pipeline_for_video_id
from video_fetcher.sources.youtube import extract_video_id


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_fetch(args: argparse.Namespace) -> int:
    raw = args.raw_dir or output_raw_root(pipeline_root(find_repo_root()))
    source = YouTubeSource()
    job = FetchJob(url=args.url, video_id=None, source="youtube")
    try:
        res = source.fetch(
            job,
            raw,
            force=args.force,
            max_retries=args.retries,
        )
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        logging.getLogger(__name__).error("fetch 失败: %s", e)
        return 1


def cmd_batch(args: argparse.Namespace) -> int:
    target = args.target
    state_path = Path(args.state_file) if args.state_file else default_state_path(target)
    raw = args.raw_dir or output_raw_root(pipeline_root(find_repo_root()))
    _, code = run_batch_download(
        target,
        state_path=state_path,
        raw_root=raw,
        resume=args.resume,
        max_workers=args.max_workers,
        skip_existing=args.skip_existing and not args.force,
        force=args.force,
        max_retries=args.retries,
    )
    return code


def cmd_pipeline(args: argparse.Namespace) -> int:
    """先下载再对每条成功项调用 run.py --full。"""
    target = args.target
    exit_code = 0

    if args.batch_mode or Path(target).is_file() or _looks_like_playlist(target):
        state_path = (
            Path(args.state_file) if args.state_file else default_state_path(target)
        )
        raw = args.raw_dir or output_raw_root(pipeline_root(find_repo_root()))
        state, dl_code = run_batch_download(
            target,
            state_path=state_path,
            raw_root=raw,
            resume=args.resume,
            max_workers=args.max_workers,
            skip_existing=args.skip_existing and not args.force,
            force=args.force,
            max_retries=args.retries,
        )
        if dl_code != 0:
            exit_code = dl_code
        items = [
            it
            for it in state.items
            if it.status in (STATUS_DONE, STATUS_SKIPPED) and it.video_id
        ]
        for it in items:
            rc = run_pipeline_for_video_id(
                it.video_id,
                keep_intermediate=args.keep_intermediate,
            )
            if rc != 0:
                exit_code = rc
                logging.error("译配失败 video_id=%s code=%d", it.video_id, rc)
        return exit_code

    # 单条 URL
    if not is_youtube_url(target):
        logging.error("pipeline 单条模式需要 YouTube URL")
        return 1
    fetch_args = argparse.Namespace(
        url=target,
        raw_dir=args.raw_dir,
        force=args.force,
        retries=args.retries,
    )
    if cmd_fetch(fetch_args) != 0:
        return 1
    rc = run_pipeline_full(
        target,
        keep_intermediate=args.keep_intermediate,
    )
    if rc == 0 and getattr(args, "relocate", False):
        vid = extract_video_id(target)
        if vid:
            raw = args.raw_dir or output_raw_root(pipeline_root(find_repo_root()))
            proc = output_processed_root(pipeline_root(find_repo_root()))
            apply_relocate_layout(raw, proc, vid)
    return rc


def cmd_workflow(args: argparse.Namespace) -> int:
    """下载 + 译配 + 归档（raw 仅 mp4；processed 双语 SRT + 硬烧配音成片）。"""
    try:
        code, _ = run_workflow(
            args.target,
            batch_mode=args.batch_mode,
            limit=args.limit,
            state_path=Path(args.state_file) if args.state_file else None,
            raw_root=args.raw_dir,
            resume=args.resume,
            max_workers=args.max_workers,
            skip_existing=args.skip_existing and not args.force,
            force=args.force,
            max_retries=args.retries,
        )
        return code
    except ValueError as e:
        logging.getLogger(__name__).error("%s", e)
        return 1


def _looks_like_playlist(target: str) -> bool:
    t = target.lower()
    return "list=" in t or "/playlist" in t or target.startswith("@")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="video_fetcher",
        description="批量拉取 YouTube 视频到 pipeline/output/raw/<id>/",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志")
    sub = p.add_subparsers(dest="command", required=True)

    pf = sub.add_parser("fetch", help="下载单条 URL（mp4 + en.vtt）")
    pf.add_argument("url", help="YouTube 视频链接")
    pf.add_argument(
        "--raw-dir",
        type=Path,
        default=None,
        help="覆盖 raw 根目录（默认 pipeline/output/raw）",
    )
    pf.add_argument("--force", action="store_true", help="已存在 mp4 仍重新下载")
    pf.add_argument("--retries", type=int, default=3, help="失败重试次数")
    pf.set_defaults(func=cmd_fetch)

    pb = sub.add_parser("batch", help="批量下载（urls.txt 或播放列表/频道 URL）")
    pb.add_argument("target", help="URL 列表文件或播放列表/频道链接")
    pb.add_argument("--state-file", default="", help="batch_state.json 路径")
    pb.add_argument("--raw-dir", type=Path, default=None)
    pb.add_argument("--resume", action="store_true", help="从已有 batch_state.json 续跑")
    pb.add_argument("--max-workers", type=int, default=1, help="并发下载数")
    pb.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="已存在 raw/<id>/<id>.mp4 则跳过（默认开启）",
    )
    pb.add_argument("--force", action="store_true")
    pb.add_argument("--retries", type=int, default=3)
    pb.set_defaults(func=cmd_batch)

    pp = sub.add_parser("pipeline", help="下载后自动 run.py --full")
    pp.add_argument(
        "target",
        help="单条 YouTube URL，或 urls.txt / 播放列表（加 --batch-mode）",
    )
    pp.add_argument(
        "--batch-mode",
        action="store_true",
        help="强制按 batch 处理 target（本地列表或播放列表）",
    )
    pp.add_argument("--state-file", default="")
    pp.add_argument("--raw-dir", type=Path, default=None)
    pp.add_argument("--resume", action="store_true")
    pp.add_argument("--max-workers", type=int, default=1)
    pp.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    pp.add_argument("--force", action="store_true")
    pp.add_argument("--retries", type=int, default=3)
    pp.add_argument(
        "--keep-intermediate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="传给 run.py --keep-intermediate（默认开启）",
    )
    pp.add_argument(
        "--relocate",
        action="store_true",
        help="译配完成后归档：processed 仅双语 SRT+硬烧成片，raw 仅 mp4",
    )
    pp.set_defaults(func=cmd_pipeline)

    pw = sub.add_parser(
        "workflow",
        help="★ 搬运全流程：拉取→译配→仅保留 raw 原片 + processed 双语字幕与硬烧成片",
    )
    pw.add_argument(
        "target",
        help="YouTube URL、@频道/videos、urls.txt 或播放列表",
    )
    pw.add_argument("--batch-mode", action="store_true")
    pw.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="频道/播放列表仅处理最近 N 条（如 5）",
    )
    pw.add_argument("--state-file", default="")
    pw.add_argument("--raw-dir", type=Path, default=None)
    pw.add_argument("--resume", action="store_true")
    pw.add_argument("--max-workers", type=int, default=1)
    pw.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    pw.add_argument("--force", action="store_true")
    pw.add_argument("--retries", type=int, default=3)
    pw.set_defaults(func=cmd_workflow)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        find_repo_root()
    except FileNotFoundError as e:
        logging.error("%s", e)
        sys.exit(2)
    code = args.func(args)
    sys.exit(code)


if __name__ == "__main__":
    main()
