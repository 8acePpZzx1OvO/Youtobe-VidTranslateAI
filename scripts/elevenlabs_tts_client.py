#!/usr/bin/env python3
"""
ElevenLabs 文本转语音（与 jin-wook-lee-96/ai-dubbing 一致：eleven_multilingual_v2）。

参考: https://github.com/jin-wook-lee-96/ai-dubbing/blob/main/src/app/api/dubbing/route.ts
需环境变量: ELEVENLABS_API_KEY；可选 ELEVENLABS_VOICE_ID（默认 Sarah: EXAVITQu4vr4xnSDxMaL）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def synthesize_elevenlabs_tts(
    text: str,
    *,
    voice_id: str | None = None,
    model_id: str = "eleven_multilingual_v2",
    timeout: float = 120.0,
) -> bytes:
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("缺少 ELEVENLABS_API_KEY，无法调用 ElevenLabs TTS")
    vid = (voice_id or os.getenv("ELEVENLABS_VOICE_ID", "").strip() or "EXAVITQu4vr4xnSDxMaL")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
    body = json.dumps(
        {
            "text": text,
            "model_id": model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ElevenLabs TTS HTTP {exc.code}: {err[:800]}") from exc
