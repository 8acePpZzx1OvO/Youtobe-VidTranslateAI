#!/usr/bin/env python3
"""
【模块】fish_speech_tts_client.py — 调用本地/自建 Fish Speech HTTP API（与官方 tools/api_server.py 一致）。
【调用方】dub_zh.py（--backend fish）。

上游仓库: https://github.com/fishaudio/fish-speech（建议克隆到 vendor/fish-speech，见 .gitignore）。
启动示例（在 fish-speech 仓库根目录，且已下载权重后）::

    python tools/api_server.py --listen 127.0.0.1:8888

请求格式与官方 tools/api_client.py 一致：POST /v1/tts?format=msgpack，body 为 msgpack，
可选 Bearer：YOUTOBE_FISH_SPEECH_API_KEY 与服务器 --api-key 一致。
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# 与官方 api_client 默认 chunk_length 对齐
_DEFAULT_CHUNK = 300


def fish_speech_base_url() -> str:
    return (os.getenv("YOUTOBE_FISH_SPEECH_URL", "http://127.0.0.1:8888").strip().rstrip("/"))


def fish_speech_tts_url() -> str:
    base = fish_speech_base_url()
    q = urllib.parse.urlencode({"format": "msgpack"})
    return f"{base}/v1/tts?{q}"


def fish_speech_health_url() -> str:
    return f"{fish_speech_base_url()}/v1/health"


def fish_speech_available() -> bool:
    try:
        req = urllib.request.Request(
            fish_speech_health_url(),
            method="GET",
            headers={},
        )
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            return 200 <= getattr(resp, "status", resp.getcode()) < 300
    except Exception:
        return False


def _pack_msgpack(body: dict[str, Any]) -> bytes:
    try:
        import ormsgpack

        return ormsgpack.packb(body)
    except ImportError:
        import msgpack

        return msgpack.packb(body)


def _delivery_prefix_for_fish(dstyle: dict[str, Any] | None) -> str:
    """将现有 delivery 粗映射为 Fish S2 可读的短指令前缀（可选）。"""
    if not dstyle:
        return ""
    try:
        st = float(dstyle.get("eleven_stability", 0.5) or 0.5)
        dr = float(dstyle.get("rate_delta_pct", 0) or 0)
    except (TypeError, ValueError):
        return ""
    tags: list[str] = []
    if st < 0.38:
        tags.append("[expressive]")
    elif st > 0.62:
        tags.append("[calm]")
    if dr >= 4:
        tags.append("[slightly faster]")
    elif dr <= -4:
        tags.append("[slightly slower]")
    return "".join(tags)


def build_fish_tts_payload(
    text: str,
    *,
    reference_id: str | None,
    dstyle: dict[str, Any] | None,
) -> dict[str, Any]:
    prefix = _delivery_prefix_for_fish(dstyle)
    body: dict[str, Any] = {
        "text": f"{prefix}{text}" if prefix else text,
        "references": [],
        "format": "wav",
        "latency": (os.getenv("YOUTOBE_FISH_SPEECH_LATENCY", "normal").strip() or "normal"),
        "max_new_tokens": int(os.getenv("YOUTOBE_FISH_SPEECH_MAX_NEW_TOKENS", "1024") or "1024"),
        "chunk_length": int(os.getenv("YOUTOBE_FISH_SPEECH_CHUNK_LENGTH", str(_DEFAULT_CHUNK)) or _DEFAULT_CHUNK),
        "top_p": float(os.getenv("YOUTOBE_FISH_SPEECH_TOP_P", "0.8") or "0.8"),
        "repetition_penalty": float(
            os.getenv("YOUTOBE_FISH_SPEECH_REP_PENALTY", "1.1") or "1.1"
        ),
        "temperature": float(os.getenv("YOUTOBE_FISH_SPEECH_TEMPERATURE", "0.8") or "0.8"),
        "streaming": False,
        "use_memory_cache": (
            os.getenv("YOUTOBE_FISH_SPEECH_MEMORY_CACHE", "off").strip() or "off"
        ),
    }
    if reference_id and str(reference_id).strip():
        body["reference_id"] = str(reference_id).strip()
    seed_raw = os.getenv("YOUTOBE_FISH_SPEECH_SEED", "").strip()
    if seed_raw:
        try:
            body["seed"] = int(seed_raw)
        except ValueError:
            pass
    return body


def synthesize_fish_speech_wav(
    text: str,
    out_wav: Path,
    dstyle: dict[str, Any] | None,
    *,
    reference_id: str | None = None,
) -> None:
    """单条文本 → WAV（由 Fish Speech 服务端采样率决定，pydub 后续可重采样）。"""
    ref = (
        reference_id
        if reference_id is not None
        else (os.getenv("YOUTOBE_FISH_SPEECH_REFERENCE_ID", "").strip() or None)
    )
    body = build_fish_tts_payload(text, reference_id=ref, dstyle=dstyle)
    payload = _pack_msgpack(body)
    headers = {
        "Content-Type": "application/msgpack",
        "Accept": "*/*",
    }
    ak = os.getenv("YOUTOBE_FISH_SPEECH_API_KEY", "").strip()
    if ak:
        headers["Authorization"] = f"Bearer {ak}"
    req = urllib.request.Request(
        fish_speech_tts_url(),
        data=payload,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=300.0) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            pass
        raise RuntimeError(
            f"Fish Speech HTTP {e.code}: {e.reason}\n{detail}"
        ) from e
    if not data or len(data) < 64:
        raise RuntimeError("Fish Speech 返回空或过短音频")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    out_wav.write_bytes(data)
