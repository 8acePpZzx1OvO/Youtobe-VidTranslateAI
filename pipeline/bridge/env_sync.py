"""将 pipeline/.env 同步到 VideoLingo config.yaml（API、语言、TTS）。"""

from __future__ import annotations

import os
from pathlib import Path

from ruamel.yaml import YAML

PIPE = Path(__file__).resolve().parent.parent
CONFIG = PIPE / "config.yaml"


def _yaml():
    y = YAML()
    y.preserve_quotes = True
    return y


def sync_env_to_config() -> None:
    if not CONFIG.is_file():
        return
    y = _yaml()
    with CONFIG.open("r", encoding="utf-8") as f:
        data = y.load(f) or {}

    api = data.setdefault("api", {})
    if os.getenv("DEEPSEEK_API_KEY", "").strip():
        api["key"] = os.environ["DEEPSEEK_API_KEY"].strip()
        base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
        if not base.endswith("/v1"):
            base = base.rstrip("/") + "/v1"
        api["base_url"] = base
        model = os.getenv("DEEPSEEK_TRANSLATION_MODEL", "deepseek-v4-flash").strip()
        api["model"] = model or "deepseek-v4-flash"
    elif os.getenv("YOUTOBE_LLM_API_KEY", "").strip():
        api["key"] = os.environ["YOUTOBE_LLM_API_KEY"].strip()
        api["base_url"] = os.getenv(
            "YOUTOBE_LLM_BASE_URL", "https://api.siliconflow.cn/v1"
        ).strip()
        api.setdefault("model", os.getenv("YOUTOBE_LLM_MODEL", ""))
    elif os.getenv("OPENAI_API_KEY", "").strip():
        api["key"] = os.environ["OPENAI_API_KEY"].strip()
        api["base_url"] = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()

    data["target_language"] = os.getenv("VIDEOLINGO_TARGET_LANGUAGE", "简体中文")

    tts = os.getenv("VIDEOLINGO_TTS_METHOD", "").strip()
    if tts:
        data["tts_method"] = tts
    elif os.getenv("YOUTOBE_EDGE_TTS_PROXY") or os.getenv("AZURE_SPEECH_KEY"):
        data["tts_method"] = "edge_tts"

    edge = data.setdefault("edge_tts", {})
    if os.getenv("VIDEOLINGO_EDGE_VOICE", "").strip():
        edge["voice"] = os.environ["VIDEOLINGO_EDGE_VOICE"]

    ytb = data.setdefault("youtube", {})
    cookies = os.getenv("VIDEOLINGO_YTDLP_COOKIES", "").strip()
    if cookies:
        ytb["cookies_path"] = cookies

    res = os.getenv("VIDEOLINGO_YTB_RESOLUTION", "").strip()
    if res:
        data["ytb_resolution"] = res

    if os.getenv("VIDEOLINGO_DEMUCS", "").strip().lower() in ("0", "false", "no"):
        data["demucs"] = False
    elif os.getenv("VIDEOLINGO_DEMUCS", "").strip().lower() in ("1", "true", "yes"):
        data["demucs"] = True

    mix = data.setdefault("dub_mix", {})
    kv = os.getenv("VIDEOLINGO_KEEP_ORIGINAL_VOCAL", "").strip().lower()
    if kv in ("0", "false", "no"):
        mix["keep_original_vocal"] = False
    elif kv in ("1", "true", "yes"):
        mix["keep_original_vocal"] = True
    vol = os.getenv("VIDEOLINGO_ORIGINAL_VOCAL_VOLUME", "").strip()
    if vol:
        mix["original_vocal_volume"] = float(vol)
    bg = os.getenv("VIDEOLINGO_DUB_BACKGROUND_VOLUME", "").strip()
    if bg:
        mix["background_volume"] = float(bg)

    with CONFIG.open("w", encoding="utf-8") as f:
        y.dump(data, f)


def apply_proxy_from_env() -> None:
    """仅对 YouTube 下载注入 YOUTOBE_YTDLP_PROXY（设置 VIDEOLINGO_NO_YTDLP_PROXY=1 可禁用）。"""
    if os.getenv("VIDEOLINGO_NO_YTDLP_PROXY", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
            os.environ.pop(key, None)
        return
    proxy = os.getenv("YOUTOBE_YTDLP_PROXY", "").strip()
    if proxy:
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["HTTP_PROXY"] = proxy
