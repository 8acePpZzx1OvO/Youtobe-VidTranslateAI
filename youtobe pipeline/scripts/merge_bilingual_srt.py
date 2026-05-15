#!/usr/bin/env python3
"""
【模块】merge_bilingual_srt.py — 英/中两条 SRT 合并为双语 SRT（必要时按时间轴拆分长行）。
【调用方】命令行；run.py、finish_outputs.py 子进程调用。

合并英文与中文 SRT 为双语 SRT（每条最多两行：英一行 + 中一行）。

过长时在时间轴上拆成多条连续字幕；断行优先逗号/句号等自然边界，并避免英文在介词、冠词处「悬垂」。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import pysrt
except ImportError:
    print("请先安装: pip install pysrt", file=sys.stderr)
    sys.exit(1)


def _norm_one_line(s: str) -> str:
    if not s or not s.strip():
        return ""
    t = s.replace("\r\n", "\n").replace("\r", "\n")
    parts = [p.strip() for p in t.split("\n") if p.strip()]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _ms_time(ms: int) -> pysrt.SubRipTime:
    return pysrt.SubRipTime(milliseconds=max(0, int(ms)))


# 左片若以这些词结尾，多为「悬垂」碎片（如 interested in 被切成 interested | in），尽量避免。
_SPLIT_BAD_EN_TAILS = frozenset(
    """
    a an the in on at to of for with as by or and but if is are was were
    that this these those which who whom whose when where while from into
    onto than then so such no nor not be been being have has had do does
    did will would could should may might must can about into over under
    """.split()
)


def _en_tail_word_before_space(en: str, sp: int) -> str:
    """sp 为要断开处的空格下标，取左片最后一个英文 token（小写）。"""
    left = en[:sp].rstrip()
    if not left:
        return ""
    m = re.search(r"([\w'-]+)\s*$", left)
    return (m.group(1) or "").lower() if m else ""


def _score_en_space_break(en: str, sp: int, ideal: int) -> float:
    """越高越好；sp 为空格下标，左 en[:sp]，右 en[sp+1:]."""
    if sp <= 0 or sp >= len(en) - 1 or en[sp] != " ":
        return -1e9
    left = en[:sp].rstrip()
    right = en[sp + 1 :].strip()
    if len(left) < 3 or len(right) < 2:
        return -1e9
    score = 0.0
    tw = _en_tail_word_before_space(en, sp)
    if tw in _SPLIT_BAD_EN_TAILS:
        score -= 120.0
    if left[-1] in ",;:":
        score += 28.0
    if re.search(r"[.!?…]\s*$", left):
        score += 45.0
    # 略鼓励在从句逗号后断开
    if left.endswith(","):
        score += 18.0
    score -= abs(sp - ideal) * 0.02
    return score


def _split_en_two(en: str, ratio: float = 0.5) -> tuple[str, str]:
    """在英文约 ratio 处断开；优先自然空格 + 避免介词/冠词悬垂，其次才是几何中分。"""
    en = en.strip()
    if not en:
        return "", ""
    if len(en) <= 1 or " " not in en:
        k = max(1, min(len(en) - 1, int(len(en) * ratio)))
        return en[:k].strip(), en[k:].strip()
    ideal = max(2, min(len(en) - 2, int(len(en) * ratio)))
    lo = max(1, ideal - max(24, len(en) // 5))
    hi = min(len(en) - 2, ideal + max(28, len(en) // 4))
    best_sp = -1
    best_sc = -1e8
    for sp in range(lo, hi + 1):
        if en[sp] != " ":
            continue
        sc = _score_en_space_break(en, sp, ideal)
        if sc > best_sc:
            best_sc, best_sp = sc, sp
    if best_sp > 0:
        a, b = en[:best_sp].strip(), en[best_sp + 1 :].strip()
        if a and b:
            return a, b
    # 回退：与原逻辑相近的最近空格
    sp = en.rfind(" ", 0, ideal + 1)
    if sp <= 0:
        sp = en.find(" ", ideal)
    if sp <= 0 or sp >= len(en) - 1:
        mid = len(en) // 2
        return en[:mid].strip(), en[mid:].strip()
    a, b = en[:sp].strip(), en[sp + 1 :].strip()
    if not a or not b:
        mid = max(1, len(en) // 2)
        return en[:mid].strip(), en[mid:].strip()
    return a, b


def _split_zh_two(zh: str, en_ratio: float) -> tuple[str, str]:
    """按与英文前半长度比例，在中文标点或字符边界切两半。"""
    zh = zh.strip()
    if not zh:
        return "", ""
    if len(zh) <= 1:
        return zh, ""
    target = max(1, min(len(zh) - 1, int(len(zh) * en_ratio)))
    puncts = "，、；：。！？"
    for i in range(target, 0, -1):
        if zh[i - 1] in puncts:
            a, b = zh[:i].strip(), zh[i:].strip()
            if a and b:
                return a, b
    for i in range(target, len(zh)):
        if zh[i] in puncts:
            a, b = zh[: i + 1].strip(), zh[i + 1 :].strip()
            if a and b:
                return a, b
    if target < 1 or target >= len(zh):
        mid = len(zh) // 2
        return zh[:mid].strip(), zh[mid:].strip()
    return zh[:target].strip(), zh[target:].strip()


def _needs_split(en: str, zh: str, max_en: int, max_zh: int) -> bool:
    # 略放宽：略超上限仍单行展示，减少「半句占半屏」的碎条感（硬烧可读性优先）
    return len(en) > max_en + 12 or len(zh) > max_zh + 8


def _bisect_bilingual(
    en: str,
    zh: str,
    t0: pysrt.SubRipTime,
    t1: pysrt.SubRipTime,
    max_en: int,
    max_zh: int,
    depth: int,
    *,
    min_seg_ms: int = 420,
) -> list[tuple[pysrt.SubRipTime, pysrt.SubRipTime, str, str]]:
    """返回若干 (start, end, en_line, zh_line)，每条仅两行文本。"""
    en = _norm_one_line(en)
    zh = _norm_one_line(zh)
    o0, o1 = t0.ordinal, t1.ordinal
    if o1 <= o0:
        o1 = o0 + 80

    if not _needs_split(en, zh, max_en, max_zh) or depth >= 6:
        if not en and not zh:
            return []
        return [(t0, t1, en, zh)]

    en_a, en_b = _split_en_two(en, 0.52)
    if not en_b:
        return [(t0, t1, en, zh)]
    ratio = len(en_a) / max(len(en), 1)
    zh_a, zh_b = _split_zh_two(zh, ratio)
    if not zh_b and zh:
        zh_a, zh_b = _split_zh_two(zh, 0.5)
    mid = int(o0 + (o1 - o0) * ratio)
    if mid <= o0 + min_seg_ms:
        mid = o0 + min_seg_ms
    if mid >= o1 - min_seg_ms:
        mid = o1 - min_seg_ms
    if mid <= o0 + 40 or mid >= o1 - 40 or (mid - o0) < min_seg_ms or (o1 - mid) < min_seg_ms:
        return [(t0, t1, en, zh)]
    t_mid = _ms_time(mid)

    left = _bisect_bilingual(en_a, zh_a, t0, t_mid, max_en, max_zh, depth + 1)
    right = _bisect_bilingual(en_b, zh_b, t_mid, t1, max_en, max_zh, depth + 1)
    return left + right


def merge(
    en_path: Path,
    zh_path: Path,
    out_path: Path,
    *,
    max_en_chars: int = 80,
    max_zh_chars: int = 40,
) -> None:
    en = pysrt.open(str(en_path))
    zh = pysrt.open(str(zh_path))
    n_en = len(en)
    n_zh = len(zh)
    if n_zh < n_en:
        print(
            f"警告: 英文字幕 {n_en} 条，中文仅 {n_zh} 条；"
            f"第 {n_zh + 1}–{n_en} 条中文留空。请续译: "
            f'python scripts/translate_srt.py "{en_path}" "{zh_path}" --resume',
            file=sys.stderr,
        )
    elif n_zh > n_en:
        print(
            f"警告: 中文 {n_zh} 条多于英文 {n_en} 条，仅合并前 {n_en} 条。",
            file=sys.stderr,
        )

    pieces: list[tuple[pysrt.SubRipTime, pysrt.SubRipTime, str, str]] = []
    for i in range(n_en):
        e = en[i]
        zt = zh[i].text if i < n_zh else ""
        en_one = _norm_one_line(e.text)
        zh_one = _norm_one_line(zt)
        if not en_one and not zh_one:
            continue
        sub = _bisect_bilingual(
            en_one,
            zh_one,
            e.start,
            e.end,
            max_en_chars,
            max_zh_chars,
            0,
        )
        pieces.extend(sub)

    out = pysrt.SubRipFile()
    for idx, (st, ed, etx, ztx) in enumerate(pieces, start=1):
        body = f"{etx}\n{ztx}" if ztx else etx
        out.append(pysrt.SubRipItem(idx, st, ed, body))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(out_path), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="合并英中 SRT 为双语（每屏英一行+中一行；过长则拆条）"
    )
    ap.add_argument("en", type=Path, help="英文 SRT")
    ap.add_argument("zh", type=Path, help="中文 SRT")
    ap.add_argument("out", type=Path, nargs="?", default=None)
    ap.add_argument(
        "--max-en-chars",
        type=int,
        default=int(os.getenv("BILINGUAL_MAX_EN_CHARS", "80")),
        help="单行英文超过该长度则拆条（可用环境变量 BILINGUAL_MAX_EN_CHARS）",
    )
    ap.add_argument(
        "--max-zh-chars",
        type=int,
        default=int(os.getenv("BILINGUAL_MAX_ZH_CHARS", "40")),
        help="单行中文超过该长度则拆条（可用环境变量 BILINGUAL_MAX_ZH_CHARS）",
    )
    args = ap.parse_args()
    outp = args.out or args.en.with_name(args.en.stem + ".bilingual.srt")
    merge(
        args.en,
        args.zh,
        outp,
        max_en_chars=max(32, args.max_en_chars),
        max_zh_chars=max(14, args.max_zh_chars),
    )
    print(str(outp.resolve()))


if __name__ == "__main__":
    main()
