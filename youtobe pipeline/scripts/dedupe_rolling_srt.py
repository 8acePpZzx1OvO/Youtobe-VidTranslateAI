#!/usr/bin/env python3
"""
【模块】dedupe_rolling_srt.py — CLI：对已有 SRT 做滚动重复去重（委托 rolling_caption_dedupe）。
【调用方】命令行独立维护字幕；可选在翻译前手动跑。

对已有 SRT 做 YouTube 式「滚动字幕」去重（就地或输出到新文件）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from rolling_caption_dedupe import dedupe_srt_file  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(
        description="去掉相邻字幕中重复的英文滚动片段，并删除去重后为空的条目。"
    )
    ap.add_argument("srt", type=Path, help="输入 .srt")
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="输出路径（默认覆盖输入）",
    )
    ap.add_argument(
        "--backup",
        action="store_true",
        help="覆盖前将原文件复制为 .bak",
    )
    args = ap.parse_args()
    src = args.srt
    if not src.exists():
        print(f"文件不存在: {src}", file=sys.stderr)
        sys.exit(2)
    out = args.output or src
    if args.backup and out == src:
        shutil.copy2(src, src.with_suffix(src.suffix + ".bak"))
    if out != src:
        shutil.copy2(src, out)
        target = out
    else:
        target = src
    kept, removed = dedupe_srt_file(target)
    print(f"完成: 保留 {kept} 条，删去 {removed} 条空/重复尾。", file=sys.stderr)
    print(str(target.resolve()))


if __name__ == "__main__":
    main()
