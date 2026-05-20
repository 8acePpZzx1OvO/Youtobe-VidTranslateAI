#!/usr/bin/env python3
"""
VideoLingo 译配入口（替代原 vidtranslate 流水线）。

  python run.py <YouTube_URL> --full
  python run.py --finalize-only <video_id>   # 仅重新导出已有 VideoLingo output/
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parent


def _fix_win_console() -> None:
    """避免 Windows GBK 终端下 rich 输出 emoji 触发 UnicodeEncodeError。"""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "utf-8")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PIPE / ".env", override=False)


def main(argv: list[str] | None = None) -> int:
    _fix_win_console()
    _load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )

    p = argparse.ArgumentParser(description="VideoLingo 译配（youtube-vid-translate 适配层）")
    p.add_argument("url", nargs="?", help="YouTube URL")
    p.add_argument("--full", action="store_true", help="字幕 + 配音 + 成片")
    p.add_argument("--bilingual", action="store_true", help="仅字幕流程（无配音）")
    p.add_argument("--raw-dir", type=Path, default=PIPE / "output" / "raw")
    p.add_argument("--proc-dir", type=Path, default=PIPE / "output" / "processed")
    p.add_argument("--keep-intermediate", action="store_true", help="保留 VideoLingo output/ 工作目录")
    p.add_argument(
        "--fresh",
        action="store_true",
        help="清空 pipeline/output/ 后重新下载（默认保留已有素材并跳过下载）",
    )
    p.add_argument(
        "--finalize-only",
        metavar="VIDEO_ID",
        help="不重新跑流程，仅从 pipeline/output/ 导出到 raw/processed",
    )
    args = p.parse_args(argv)

    from bridge.env_sync import apply_proxy_from_env, sync_env_to_config

    sync_env_to_config()

    if args.finalize_only:
        from bridge.export_layout import export_to_repo_layout

        layout = export_to_repo_layout(
            args.finalize_only.strip(),
            raw_root=args.raw_dir,
            proc_root=args.proc_dir,
            prefer_dub=True,
        )
        logging.info("finalize-only: %s", layout)
        return 0

    if not args.url:
        p.error("需要 url，或使用 --finalize-only")

    with_dub = args.full or not args.bilingual
    if not args.full and not args.bilingual:
        with_dub = False

    from bridge.runner import run_full

    try:
        layout = run_full(
            args.url.strip(),
            with_dub=with_dub,
            raw_root=args.raw_dir,
            proc_root=args.proc_dir,
            fresh=args.fresh,
        )
        logging.info("完成: %s", layout)
        return 0
    except Exception:
        logging.exception("VideoLingo 流程失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
