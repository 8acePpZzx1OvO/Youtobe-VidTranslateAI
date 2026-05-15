#!/usr/bin/env python3
"""
【模块】subtitle_reading_time.py — 中英字幕条「槽位时长 vs 中文朗读负载」估算与分级（ok/over/under）。
【调用方】translate_srt.py（翻译后对齐）；dub_zh.py（英文轴下配音前 over/under 改写，与 translate 共用阈值）。

估算「英文字幕槽位时长」与「中文口播/字幕朗读时长」是否匹配，供翻译后对齐与报表。

模型（与配音 YOUTOBE_DUB_CPS_TARGET 一致的量纲）：
- 以汉字等效 timing units 近似 TTS 负载；units / CPS ≈ 预估朗读秒数。
- 与英文字幕 on-screen 时长 slot_sec 比较：过长易吞字/截断，过短易在槽内留白、听感脱节。
- 可选 `YOUTOBE_READING_UNITS_TTS_BIAS`>1：略抬高预估负载，使边界句更易被判为 over（提前压缩，减轻 TTS 吞字）。
- `YOUTOBE_TRANSLATE_READING_MIN_UNDER_SLOT`：低于该槽位秒数不做 under 扩充（极短句不硬灌水）。
"""

from __future__ import annotations

import os
import re
from typing import Literal

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII = re.compile(r"[A-Za-z0-9]+")
_PAUSE = re.compile(r"[，。！？、；：,.!?;:]")
_DIGIT = re.compile(r"\d")

Issue = Literal["skip", "ok", "over", "under"]


def slot_seconds_from_sub(sub) -> float:
    """单条字幕在画面上的时长（秒），下限避免除零。"""
    try:
        ms = int(sub.end.ordinal) - int(sub.start.ordinal)
    except (TypeError, ValueError, AttributeError):
        return 0.2
    return max(0.04, ms / 1000.0)


def zh_timing_units(zh: str) -> float:
    """
    中文稿的「朗读负载」近似值（非严格音节；与 CPS 配套使用）。
    CJK 权重 1；拉丁/数字略低但数字串在 TTS 中常略拖拍；句读略增停顿成本。
    """
    t = (zh or "").replace("\n", " ").strip()
    if not t:
        return 0.0
    cjk = len(_CJK.findall(t))
    ascii_chars = sum(len(w) for w in _ASCII.findall(t))
    digits = len(_DIGIT.findall(t))
    pauses = len(_PAUSE.findall(t))
    # 数字在中文 TTS 中常略拖拍（与 ascii 权重有少量重叠，略偏保守）
    digit_extra = digits * 0.04
    return max(0.2, cjk + ascii_chars * 0.34 + pauses * 0.22 + digit_extra)


def estimated_zh_seconds(zh: str, cps: float) -> float:
    cps = max(0.8, float(cps))
    return zh_timing_units(zh) / cps


def classify_reading_alignment(
    zh: str,
    slot_sec: float,
    *,
    cps: float,
    over_ratio: float,
    under_ratio: float,
    min_slot_to_check: float = 0.22,
    min_slot_for_under: float = 0.72,
    units_tts_bias: float = 1.0,
) -> Issue:
    """
    判定单条：中文预估朗读时长 vs 英文槽位。
    - over: 预估明显长于槽位（易吞字 / TTS 被压）
    - under: 槽位 ≥ min_slot_for_under 且预估过短（句内易留白、与下句间易显停顿）
    - skip: 槽位极短或空行，不对齐
    """
    slot_sec = max(slot_sec, 0.04)
    if slot_sec < min_slot_to_check:
        return "skip"
    zh = (zh or "").strip()
    if not zh:
        return "skip"
    bias = max(1.0, min(float(units_tts_bias), 1.35))
    u = zh_timing_units(zh) * bias
    est = u / max(0.8, float(cps))
    ratio = est / slot_sec if slot_sec > 1e-6 else 1.0
    if ratio > over_ratio:
        return "over"
    if slot_sec >= min_slot_for_under and ratio < under_ratio:
        return "under"
    return "ok"


def env_float(name: str, default: float) -> float:
    try:
        v = float(os.getenv(name, "").strip())
        return v if v == v else default  # NaN
    except ValueError:
        return default


def reading_align_thresholds() -> tuple[float, float, float, float, float]:
    """
    (cps, over_ratio, under_ratio, min_slot_for_under, units_tts_bias)。
    默认可读性：略紧的 over、略高的 under 判定，减少吞字与中长槽内干等。
    """
    cps = env_float("YOUTOBE_TRANSLATE_READING_CPS", env_float("YOUTOBE_DUB_CPS_TARGET", 3.45))
    cps = max(2.2, min(cps, 5.5))
    over_r = env_float("YOUTOBE_TRANSLATE_READING_OVER_RATIO", 1.05)
    over_r = max(1.02, min(over_r, 1.45))
    under_r = env_float("YOUTOBE_TRANSLATE_READING_UNDER_RATIO", 0.82)
    under_r = max(0.45, min(under_r, 0.95))
    min_u = env_float("YOUTOBE_TRANSLATE_READING_MIN_UNDER_SLOT", 0.72)
    min_u = max(0.18, min(min_u, 2.2))
    bias = env_float("YOUTOBE_READING_UNITS_TTS_BIAS", 1.04)
    bias = max(1.0, min(bias, 1.28))
    return cps, over_r, under_r, min_u, bias


def scan_subtitle_pairs(
    subs_en: list,
    zh_lines: list[str],
    *,
    cps: float | None = None,
    over_ratio: float | None = None,
    under_ratio: float | None = None,
    min_slot_for_under: float | None = None,
    units_tts_bias: float | None = None,
) -> list[dict]:
    """
    扫描整轨字幕，返回每条诊断（索引从 0 起）。
    zh_lines 与 subs_en 须等长对齐。
    """
    if (
        cps is None
        or over_ratio is None
        or under_ratio is None
        or min_slot_for_under is None
        or units_tts_bias is None
    ):
        cps, over_ratio, under_ratio, min_slot_for_under, units_tts_bias = (
            reading_align_thresholds()
        )
    out: list[dict] = []
    n = min(len(subs_en), len(zh_lines))
    for i in range(n):
        sub = subs_en[i]
        zh = zh_lines[i]
        slot = slot_seconds_from_sub(sub)
        issue = classify_reading_alignment(
            zh,
            slot,
            cps=cps,
            over_ratio=over_ratio,
            under_ratio=under_ratio,
            min_slot_for_under=min_slot_for_under,
            units_tts_bias=units_tts_bias,
        )
        bias = max(1.0, min(float(units_tts_bias), 1.35))
        u_eff = zh_timing_units(zh) * bias
        est = u_eff / max(0.8, float(cps))
        ratio = est / slot if slot > 1e-6 else 1.0
        ideal_u = slot * cps
        out.append(
            {
                "index": i,
                "slot_sec": round(slot, 4),
                "zh_units": round(zh_timing_units(zh), 2),
                "zh_units_eff": round(u_eff, 2),
                "est_sec": round(est, 3),
                "ratio": round(ratio, 3),
                "ideal_units": round(ideal_u, 2),
                "issue": issue,
            }
        )
    return out


def summarize_scan(rows: list[dict]) -> tuple[int, int, int, int]:
    """skip, ok, over, under 计数。"""
    sk = ok = ov = un = 0
    for r in rows:
        t = r["issue"]
        if t == "skip":
            sk += 1
        elif t == "ok":
            ok += 1
        elif t == "over":
            ov += 1
        else:
            un += 1
    return sk, ok, ov, un
