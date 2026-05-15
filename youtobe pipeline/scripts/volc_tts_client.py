#!/usr/bin/env python3
"""
【模块】volc_tts_client.py — 火山引擎 OpenSpeech 流式 TTS HTTP 客户端，返回音频字节。
【调用方】dub_zh.py（--backend volc）。

火山引擎（字节 OpenSpeech）单向流式 TTS HTTP 客户端。

实现思路融合自开源参考 video-Zebra-china（MIT 风格可复用逻辑）:
https://github.com/jiayuqi7813/video-Zebra-china
需环境变量: VOLCENGINE_TTS_API_KEY；可选 VOLCENGINE_TTS_RESOURCE_ID。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request


def synthesize_volc_tts(
    text: str,
    *,
    speaker: str,
    response_format: str = "wav",
    sample_rate: int = 24000,
) -> bytes:
    api_key = os.getenv("VOLCENGINE_TTS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("缺少 VOLCENGINE_TTS_API_KEY，无法调用火山 TTS")
    resource_id = (
        os.getenv("VOLCENGINE_TTS_RESOURCE_ID", "volc.service_type.10029").strip()
        or "volc.service_type.10029"
    )
    body = json.dumps(
        {
            "req_params": {
                "text": text,
                "speaker": speaker,
                "audio_params": {
                    "format": response_format,
                    "sample_rate": sample_rate,
                },
                "additions": json.dumps(
                    {
                        "disable_markdown_filter": True,
                        "enable_language_detector": True,
                        "enable_latex_tn": True,
                        "disable_default_bit_rate": True,
                        "max_length_to_filter_parenthesis": 0,
                        "cache_config": {"text_type": 1, "use_cache": True},
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
        data=body,
        headers={
            "x-api-key": api_key,
            "X-Api-Resource-Id": resource_id,
            "Connection": "keep-alive",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"火山 TTS HTTP {exc.code}: {err_body[:800]}") from exc

    audio_parts: list[bytes] = []
    final_code: int | None = None
    final_message = ""
    for raw_line in response_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if parsed.get("data"):
            audio_parts.append(base64.b64decode(parsed["data"]))
        if "code" in parsed:
            final_code = int(parsed["code"])
            final_message = str(parsed.get("message", ""))

    if not audio_parts:
        raise RuntimeError("火山 TTS 响应中未包含音频数据")
    if final_code not in (0, 20000000):
        raise RuntimeError(f"火山 TTS 失败: code={final_code}, message={final_message}")
    return b"".join(audio_parts)
