#!/usr/bin/env python3
"""
【模块】dub_delivery_map.py — 将大模型输出的 delivery 数值夹紧到 Edge/Eleven 安全区间；供 TTS 后端映射前规范化。
【调用方】translation_clients.dub_delivery_style_batch 下游；fish_speech_tts_client 情绪前缀等。

将「朗读表达强度」数值约束到 TTS 可控参数（Edge rate/pitch、ElevenLabs voice_settings）。
大模型可输出近似连续值，在此做硬夹紧；区间略宽于早期版本，便于听感起伏（仍避免极端参数）。
"""

from __future__ import annotations


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def normalize_delivery_row(row: dict) -> tuple[float, float, float, float]:
    """
    返回 (rate_delta_pct, pitch_delta_hz, eleven_stability, eleven_similarity_boost)。
    row 键名兼容 snake_case。
    """
    r = float(row.get("rate_delta_pct", row.get("rate_delta", 0)) or 0)
    p = float(row.get("pitch_delta_hz", row.get("pitch_delta", 0)) or 0)
    st = float(row.get("eleven_stability", row.get("stability", 0.5)) or 0.5)
    sim = float(
        row.get("eleven_similarity_boost", row.get("similarity_boost", 0.75)) or 0.75
    )
    return (
        clamp(r, -12.0, 12.0),
        clamp(p, -10.0, 10.0),
        clamp(st, 0.22, 0.78),
        clamp(sim, 0.52, 0.92),
    )
