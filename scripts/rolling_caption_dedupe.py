#!/usr/bin/env python3
"""
YouTube 自动字幕等 WebVTT 常为「滚动窗口」：相邻 cue 大量重复同一串英文。
对**已按时间排序**的字幕文本列表做去重，每条只保留相对上一条的**新增**片段，
便于 SRT 阅读、翻译与配音（与 translate_srt / merge 一致）。

对已是「一句一行」的字幕再次调用一般无害（词边界重叠很短，不会误删）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


def _norm_token(t: str) -> str:
    """用于比对：小写 + 去掉首尾非字母数字（保留中间撇号等）。"""
    s = t.strip().lower()
    s = re.sub(r"^[^\w']+|[^\w']+$", "", s)
    return s


def _tokens(s: str) -> list[str]:
    return [x for x in (s or "").split() if x]


def strip_roll_overlap(prev_full: str, curr_full: str) -> str:
    """
    prev_full / curr_full 为相邻两条「滚动条」全文。
    返回 curr 中相对 prev 的新增部分（去掉与 prev 尾部重合的前缀词）。
    """
    pw = _tokens(prev_full)
    cw = _tokens(curr_full)
    if not cw:
        return ""
    if not pw:
        return " ".join(cw).strip()
    max_k = min(len(pw), len(cw))
    best = 0
    for k in range(max_k, 0, -1):
        ok = True
        for i in range(k):
            if _norm_token(pw[-k + i]) != _norm_token(cw[i]):
                ok = False
                break
        if ok:
            best = k
            break
    out = cw[best:]
    return " ".join(out).strip()


def dedupe_rolling_lines(lines: Iterable[str]) -> list[str]:
    """顺序去重，返回与输入条数相同长度的列表（空条保持空）。"""
    prev_accum = ""
    out: list[str] = []
    for line in lines:
        raw = (line or "").strip()
        if not raw:
            out.append("")
            continue
        delta = strip_roll_overlap(prev_accum, raw)
        prev_accum = raw
        out.append(delta)
    return out


def dedupe_srt_file(path: Path, *, encoding: str = "utf-8") -> tuple[int, int]:
    """
    就地重写 SRT：对每条文本做滚动去重，删除去重后为空的条目并重新编号。
    返回 (保留条数, 删除条数)。
    """
    import pysrt

    subs = list(pysrt.open(str(path), encoding=encoding))
    texts = [re.sub(r"\s+", " ", (s.text or "").replace("\n", " ")).strip() for s in subs]
    deduped = dedupe_rolling_lines(texts)
    out = pysrt.SubRipFile()
    kept = 0
    for s, d in zip(subs, deduped):
        t = (d or "").strip()
        if not t:
            continue
        kept += 1
        s.index = kept
        s.text = t
        out.append(s)
    removed = len(subs) - kept
    out.save(str(path), encoding=encoding)
    return kept, removed
