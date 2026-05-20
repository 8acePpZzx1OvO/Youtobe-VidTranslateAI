"""调用 VideoLingo core 步骤（非 Streamlit）。"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PIPE = Path(__file__).resolve().parent.parent


def _ensure_cwd() -> None:
    import os

    os.chdir(PIPE)
    if str(PIPE) not in sys.path:
        sys.path.insert(0, str(PIPE))


def _clean_vl_output() -> None:
    out = PIPE / "output"
    if out.is_dir():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)


def run_text_pipeline() -> None:
    from core import (
        _2_asr,
        _3_1_split_nlp,
        _3_2_split_meaning,
        _4_1_summarize,
        _4_2_translate,
        _5_split_sub,
        _6_gen_sub,
        _7_sub_into_vid,
    )

    logger.info("VideoLingo: ASR")
    _2_asr.transcribe()
    logger.info("VideoLingo: NLP split")
    _3_1_split_nlp.split_by_spacy()
    _3_2_split_meaning.split_sentences_by_meaning()
    logger.info("VideoLingo: summarize + translate")
    _4_1_summarize.get_summary()
    _4_2_translate.translate_all()
    logger.info("VideoLingo: split subs + align")
    _5_split_sub.split_for_sub_main()
    _6_gen_sub.align_timestamp_main()
    logger.info("VideoLingo: burn subtitles")
    _7_sub_into_vid.merge_subtitles_to_video()


def run_dub_pipeline() -> None:
    from core import (
        _10_gen_audio,
        _11_merge_audio,
        _12_dub_to_vid,
        _8_1_audio_task,
        _8_2_dub_chunks,
        _9_refer_audio,
    )

    logger.info("VideoLingo: dub tasks")
    _8_1_audio_task.gen_audio_task_main()
    _8_2_dub_chunks.gen_dub_chunks()
    _9_refer_audio.extract_refer_audio_main()
    _10_gen_audio.gen_audio()
    _11_merge_audio.merge_full_audio()
    _12_dub_to_vid.merge_video_audio()


def _clear_proxy_env() -> None:
    import os

    for key in (
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "https_proxy",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        os.environ.pop(key, None)


def run_download(
    url: str,
    *,
    resolution: str | None = None,
    fresh: bool = False,
) -> Path:
    from bridge.env_sync import apply_proxy_from_env
    from core._1_ytdlp import download_video_ytdlp, find_video_files
    from core.utils.config_utils import load_key

    if fresh:
        _clean_vl_output()
    else:
        (PIPE / "output").mkdir(parents=True, exist_ok=True)

    try:
        existing = find_video_files("output")
        logger.info("VideoLingo: 已有本地视频，跳过下载: %s", existing)
        _clear_proxy_env()
        return Path(existing)
    except ValueError:
        pass

    _clear_proxy_env()
    apply_proxy_from_env()
    res = resolution or load_key("ytb_resolution")
    logger.info("VideoLingo: download %s res=%s", url, res)
    download_video_ytdlp(url, save_path="output", resolution=res)
    _clear_proxy_env()
    return Path(find_video_files("output"))


def _extract_video_id(url: str) -> str | None:
    import re
    from urllib.parse import parse_qs, urlparse

    u = (url or "").strip()
    if not u:
        return None
    parsed = urlparse(u)
    if "youtu.be" in (parsed.hostname or "").lower():
        return (parsed.path.strip("/").split("/") or [None])[0]
    if "youtube" in (parsed.hostname or "").lower():
        qs = parse_qs(parsed.query)
        if qs.get("v"):
            return qs["v"][0]
        parts = [p for p in parsed.path.split("/") if p]
        for key in ("shorts", "embed", "live"):
            if key in parts:
                i = parts.index(key)
                if i + 1 < len(parts):
                    return parts[i + 1]
    m = re.search(r"v=([A-Za-z0-9_-]{11})", u)
    return m.group(1) if m else None


def run_full(
    url: str,
    *,
    with_dub: bool = True,
    video_id: str | None = None,
    raw_root: Path | None = None,
    proc_root: Path | None = None,
    fresh: bool = False,
) -> dict:
    from bridge.export_layout import export_to_repo_layout

    _ensure_cwd()
    vid = video_id or _extract_video_id(url) or "unknown"
    raw = raw_root or (PIPE / "output" / "raw")
    proc = proc_root or (PIPE / "output" / "processed")

    run_download(url, fresh=fresh)
    _clear_proxy_env()
    run_text_pipeline()
    if with_dub:
        run_dub_pipeline()

    layout = export_to_repo_layout(vid, raw_root=raw, proc_root=proc, prefer_dub=with_dub)
    layout["video_id"] = vid
    layout["url"] = url
    return layout
