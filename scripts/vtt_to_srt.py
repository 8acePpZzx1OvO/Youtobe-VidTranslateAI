#!/usr/bin/env python3
"""将 WebVTT 转为 SRT（供翻译与烧录用）。默认去除 YouTube 自动字幕的「滚动重复」。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

try:
    import webvtt
except ImportError:
    print("请先安装: pip install webvtt-py", file=sys.stderr)
    sys.exit(1)

from rolling_caption_dedupe import dedupe_rolling_lines  # noqa: E402


def vtt_to_srt(vtt_path: Path, srt_path: Path, *, dedupe_roll: bool = True) -> None:
    cues_data: list[tuple[str, str, str]] = []
    raw_texts: list[str] = []
    for cue in webvtt.read(str(vtt_path)):
        text = re.sub(r"<[^>]+>", "", cue.text or "").strip()
        if not text:
            continue
        text = text.replace("\n", " ")
        raw_texts.append(text)
        cues_data.append((_ts(cue.start), _ts(cue.end), text))

    if dedupe_roll and raw_texts:
        deduped = dedupe_rolling_lines(raw_texts)
    else:
        deduped = raw_texts

    lines: list[str] = []
    idx = 1
    for (start, end, _raw), text in zip(cues_data, deduped):
        t = (text or "").strip()
        if not t:
            continue
        lines.append(f"{idx}\n{start} --> {end}\n{t}\n")
        idx += 1
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ts(t: str) -> str:
    """WebVTT 时间戳转为 SRT（毫秒前为逗号）。"""
    t = t.strip()
    if "." in t:
        base, ms = t.rsplit(".", 1)
        if ms.isdigit():
            ms = (ms + "000")[:3]
            return f"{base},{ms}"
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("vtt", type=Path)
    ap.add_argument("srt", type=Path, nargs="?", default=None)
    ap.add_argument(
        "--no-dedupe-roll",
        action="store_true",
        help="保留原始滚动字幕（不推荐，翻译会大量重复）",
    )
    args = ap.parse_args()
    out = args.srt or args.vtt.with_suffix(".srt")
    vtt_to_srt(args.vtt, out, dedupe_roll=not args.no_dedupe_roll)
    print(str(out.resolve()))


if __name__ == "__main__":
    main()
