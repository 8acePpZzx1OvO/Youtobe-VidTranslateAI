#!/usr/bin/env python3
"""
按英文句边界处理 SRT：在句号/问号/感叹号后的空白处切句，并按字符比例映射回原时间轴；
用于去掉 YouTube 去重后仍残留的「短语级」碎条，改善双语与配音断句。

若提供 --zh，须与合并前英文条数一致，按同一批 cue 合并中文（用「，」连接）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import pysrt
except ImportError:
    print("请先安装: pip install pysrt", file=sys.stderr)
    sys.exit(1)


def _ms_to_subrip(ms: int) -> pysrt.SubRipTime:
    return pysrt.SubRipTime(milliseconds=max(0, int(ms)))


def _char_index_to_ordinal(
    spans: list[tuple[int, int, int, int]],
    idx: int,
) -> int:
    """spans: (char_start, char_end_excl, ord_start, ord_end) 覆盖整条字幕串；idx 为 full 内字符下标。"""
    if not spans:
        return 0
    idx = max(spans[0][0], min(idx, spans[-1][1] - 1))
    for a, b, os, oe in spans:
        if a <= idx < b:
            if b - a <= 1:
                return os
            return int(os + (oe - os) * (idx - a) / (b - a))
    return spans[-1][3]


def merge_en_srt_by_sentences(
    en_path: Path,
    *,
    max_span_ms: int = 18_000,
    max_merged_chars: int = 480,
    zh_path: Path | None = None,
    encoding: str = "utf-8",
) -> tuple[int, int]:
    """
    按英文句号/问号/感叹号后空格 `(?<=[.!?])\\s+` 分句，并按字符比例映射到原时间轴。
    就地重写 en.srt；若 zh_path 与合并前英文条数一致，按「同一批 cue」合并中文（用「，」连接）。
    """
    en_subs = list(pysrt.open(str(en_path), encoding=encoding))
    n0 = len(en_subs)
    zh_subs: list | None = None
    if zh_path is not None and zh_path.exists():
        zh_subs = list(pysrt.open(str(zh_path), encoding=encoding))
        if len(zh_subs) != n0:
            print(
                f"警告: 中文 {len(zh_subs)} 条 ≠ 英文 {n0} 条，跳过中文合并，仅合并英文。",
                file=sys.stderr,
            )
            zh_subs = None

    # 每条非空 cue 在 full 中的字符区间 + 原下标（与 zh 对齐）
    full_chunks: list[str] = []
    spans: list[tuple[int, int, int, int, int]] = []  # c0,c1ex,ord0,ord1,orig_idx
    c = 0
    for orig_i, s in enumerate(en_subs):
        t = s.text.replace("\n", " ").strip()
        if not t:
            continue
        if full_chunks:
            c += 1
        c0 = c
        c += len(t)
        spans.append((c0, c, s.start.ordinal, s.end.ordinal, orig_i))
        full_chunks.append(t)
    full = " ".join(full_chunks)
    if not full.strip():
        return n0, n0

    # 按句切分：在 [.!?] 后跟空白处切开
    bounds: list[tuple[int, int, str]] = []  # char_start, char_end_excl, sentence_text
    start = 0
    for m in re.finditer(r"[.!?]\s+", full):
        e = m.end()
        core = full[start : m.start() + 1].strip()
        if core:
            bounds.append((start, m.start() + 1, core))
        start = e
    if start < len(full):
        tail = full[start:].strip()
        if tail:
            bounds.append((start, len(full), tail))

    # 仅一条非空 cue 且整串为一句：无需改写
    if len(spans) == 1 and len(bounds) == 1:
        return n0, n0

    def cue_indices_for_chars(c0: int, c1_ex: int) -> list[int]:
        out_i: list[int] = []
        for a, b, _, _, oi in spans:
            if b <= c0 or a >= c1_ex:
                continue
            out_i.append(oi)
        return out_i

    out_en = pysrt.SubRipFile()
    out_zh = pysrt.SubRipFile() if zh_subs else None
    tri_spans = [(a, b, os, oe) for a, b, os, oe, _ in spans]
    new_idx = 0
    for c0, c1_ex, sent in bounds:
        if not sent:
            continue
        span_ms = _char_index_to_ordinal(tri_spans, c1_ex - 1) - _char_index_to_ordinal(
            tri_spans, c0
        )
        if span_ms > max_span_ms or len(sent) > max_merged_chars:
            # 过长无句点：按 cue 边界硬拆成多条（尽量少条）
            subparts: list[tuple[int, int, str]] = []
            cur_s: int | None = None
            buf = ""
            for a, b, _, _, _ in spans:
                if b <= c0 or a >= c1_ex:
                    continue
                seg = full[a:b]
                if buf and len(buf) + 1 + len(seg) > max_merged_chars:
                    subparts.append((cur_s or a, a, buf.strip()))
                    cur_s, buf = a, seg
                else:
                    if not buf:
                        cur_s = a
                    buf = (buf + " " + seg).strip() if buf else seg
            if buf:
                subparts.append((cur_s, c1_ex, buf))
            for ps, pe, tx in subparts:
                if not tx.strip():
                    continue
                new_idx += 1
                o0 = _char_index_to_ordinal(tri_spans, ps)
                o1 = _char_index_to_ordinal(tri_spans, max(ps, pe - 1))
                if o1 <= o0:
                    o1 = o0 + 200
                out_en.append(
                    pysrt.SubRipItem(
                        new_idx,
                        _ms_to_subrip(o0),
                        _ms_to_subrip(o1),
                        tx.strip(),
                    )
                )
                if zh_subs is not None:
                    ids = cue_indices_for_chars(ps, pe)
                    zt = "，".join(
                        zh_subs[k].text.replace("\n", " ").strip()
                        for k in ids
                        if k < len(zh_subs) and zh_subs[k].text.strip()
                    )
                    out_zh.append(
                        pysrt.SubRipItem(
                            new_idx,
                            _ms_to_subrip(o0),
                            _ms_to_subrip(o1),
                            zt,
                        )
                    )
            continue
        new_idx += 1
        o0 = _char_index_to_ordinal(tri_spans, c0)
        o1 = _char_index_to_ordinal(tri_spans, max(c0, c1_ex - 1))
        if o1 <= o0:
            o1 = o0 + 200
        out_en.append(
            pysrt.SubRipItem(
                new_idx,
                _ms_to_subrip(o0),
                _ms_to_subrip(o1),
                sent.strip(),
            )
        )
        if zh_subs is not None:
            ids = cue_indices_for_chars(c0, c1_ex)
            zt = "，".join(
                zh_subs[k].text.replace("\n", " ").strip()
                for k in ids
                if k < len(zh_subs) and zh_subs[k].text.strip()
            )
            out_zh.append(
                pysrt.SubRipItem(
                    new_idx,
                    _ms_to_subrip(o0),
                    _ms_to_subrip(o1),
                    zt,
                )
            )

    n1 = len(out_en)
    if n1 == 0:
        return n0, n0
    en_path.parent.mkdir(parents=True, exist_ok=True)
    out_en.save(str(en_path), encoding=encoding)
    if out_zh is not None and zh_path is not None:
        out_zh.save(str(zh_path), encoding=encoding)
    return n0, n1


def main() -> None:
    ap = argparse.ArgumentParser(description="按英文句号/问号/感叹号后空白切句并重写 SRT 时间轴")
    ap.add_argument("en_srt", type=Path, help="英文 SRT（就地覆盖）")
    ap.add_argument("--zh", type=Path, default=None, help="可选：同步合并中文 SRT（条数须与英文一致）")
    ap.add_argument(
        "--max-span-ms",
        type=int,
        default=18_000,
        help="单条合并后最长时长（毫秒），防止无句号的长段撑爆",
    )
    ap.add_argument(
        "--max-chars",
        type=int,
        default=480,
        help="单条合并后英文最大字符数",
    )
    args = ap.parse_args()
    n0, n1 = merge_en_srt_by_sentences(
        args.en_srt,
        max_span_ms=args.max_span_ms,
        max_merged_chars=args.max_chars,
        zh_path=args.zh,
    )
    print(f"英文句合并: {n0} 条 → {n1} 条。", file=sys.stderr)
    print(str(args.en_srt.resolve()))


if __name__ == "__main__":
    main()
