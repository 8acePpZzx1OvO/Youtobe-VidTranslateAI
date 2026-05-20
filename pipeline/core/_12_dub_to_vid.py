import os
import platform
import subprocess

import cv2
import numpy as np
from rich.console import Console

from core._1_ytdlp import find_video_files
from core.asr_backend.audio_preprocess import normalize_audio_volume
from core.utils import *
from core.utils.models import *

console = Console()

DUB_VIDEO = "output/output_dub.mp4"
DUB_SUB_FILE = "output/dub.srt"
DUB_AUDIO = "output/dub.mp3"
NORMALIZED_DUB = "output/normalized_dub.wav"
NORMALIZED_VOCAL = "output/normalized_vocal.wav"

TRANS_FONT_SIZE = 17
TRANS_FONT_NAME = "Arial"
if platform.system() == "Linux":
    TRANS_FONT_NAME = "NotoSansCJK-Regular"
if platform.system() == "Darwin":
    TRANS_FONT_NAME = "Arial Unicode MS"

TRANS_FONT_COLOR = "&H00FFFF"
TRANS_OUTLINE_COLOR = "&H000000"
TRANS_OUTLINE_WIDTH = 1
TRANS_BACK_COLOR = "&H33000000"


def _dub_mix_setting(key: str, default):
    try:
        return load_key(f"dub_mix.{key}")
    except KeyError:
        return default


def _resolve_original_vocal_source() -> str | None:
    """Prefer Demucs-isolated vocals; fall back to raw audio from the video."""
    if os.path.isfile(_VOCAL_AUDIO_FILE):
        return _VOCAL_AUDIO_FILE
    if os.path.isfile(_RAW_AUDIO_FILE):
        return _RAW_AUDIO_FILE
    video = find_video_files()
    if not video or not os.path.isfile(video):
        return None
    extracted = "output/audio/original_from_video.wav"
    os.makedirs(os.path.dirname(extracted), exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            video,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            extracted,
        ],
        check=True,
        capture_output=True,
    )
    return extracted if os.path.isfile(extracted) else None


def _build_audio_filter(
    *,
    input_index: int,
    has_background: bool,
    has_original_vocal: bool,
    bg_volume: float,
    dub_volume: float,
    vocal_volume: float,
) -> tuple[list[str], list[str], int]:
    """Return (extra_ffmpeg_inputs, filter_lines, next_input_index)."""
    extra_inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    idx = input_index

    if has_background:
        extra_inputs.extend(["-i", _BACKGROUND_AUDIO_FILE])
        filters.append(f"[{idx}:a]volume={bg_volume}[vl_bg]")
        labels.append("[vl_bg]")
        idx += 1

    extra_inputs.extend(["-i", NORMALIZED_DUB])
    filters.append(f"[{idx}:a]volume={dub_volume}[vl_dub]")
    labels.append("[vl_dub]")
    idx += 1

    if has_original_vocal:
        extra_inputs.extend(["-i", NORMALIZED_VOCAL])
        filters.append(f"[{idx}:a]volume={vocal_volume}[vl_orig]")
        labels.append("[vl_orig]")
        idx += 1

    n = len(labels)
    filters.append(
        f"{''.join(labels)}amix=inputs={n}:duration=first:dropout_transition=3[a]"
    )
    return extra_inputs, filters, idx


def merge_video_audio():
    """Merge video, subtitles, background bed, dub, and optional original vocals."""
    VIDEO_FILE = find_video_files()
    keep_vocal = bool(_dub_mix_setting("keep_original_vocal", True))
    vocal_volume = float(_dub_mix_setting("original_vocal_volume", 0.7))
    bg_volume = float(_dub_mix_setting("background_volume", 1.0))
    dub_volume = 1.0

    if not load_key("burn_subtitles"):
        rprint(
            "[bold yellow]Warning: A 0-second black video will be generated as a "
            "placeholder as subtitles are not burned in.[/bold yellow]"
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(DUB_VIDEO, fourcc, 1, (1920, 1080))
        out.write(frame)
        out.release()
        rprint("[bold green]Placeholder video has been generated.[/bold green]")
        return

    normalize_audio_volume(DUB_AUDIO, NORMALIZED_DUB)
    has_background = os.path.isfile(_BACKGROUND_AUDIO_FILE)
    has_original_vocal = False
    if keep_vocal:
        vocal_src = _resolve_original_vocal_source()
        if vocal_src:
            normalize_audio_volume(vocal_src, NORMALIZED_VOCAL)
            has_original_vocal = True
            rprint(
                f"[cyan]🎚️ Mix original vocal at {vocal_volume:.0%} of dub level "
                f"({os.path.basename(vocal_src)})[/cyan]"
            )
        else:
            rprint("[yellow]⚠️ keep_original_vocal enabled but no vocal track found[/yellow]")

    video = cv2.VideoCapture(VIDEO_FILE)
    TARGET_WIDTH = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
    TARGET_HEIGHT = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
    video.release()
    rprint(f"[bold green]Video resolution: {TARGET_WIDTH}x{TARGET_HEIGHT}[/bold green]")

    subtitle_filter = (
        f"subtitles={DUB_SUB_FILE}:force_style='FontSize={TRANS_FONT_SIZE},"
        f"FontName={TRANS_FONT_NAME},PrimaryColour={TRANS_FONT_COLOR},"
        f"OutlineColour={TRANS_OUTLINE_COLOR},OutlineWidth={TRANS_OUTLINE_WIDTH},"
        f"BackColour={TRANS_BACK_COLOR},Alignment=2,MarginV=27,BorderStyle=4'"
    )

    audio_inputs, audio_filters, _ = _build_audio_filter(
        input_index=1,
        has_background=has_background,
        has_original_vocal=has_original_vocal,
        bg_volume=bg_volume,
        dub_volume=dub_volume,
        vocal_volume=vocal_volume,
    )

    filter_complex = (
        f"[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        f"{subtitle_filter}[v];"
        + ";".join(audio_filters)
    )

    cmd = ["ffmpeg", "-y", "-i", VIDEO_FILE, *audio_inputs, "-filter_complex", filter_complex]

    if load_key("ffmpeg_gpu"):
        rprint("[bold green]Using GPU acceleration...[/bold green]")
        cmd.extend(["-map", "[v]", "-map", "[a]", "-c:v", "h264_nvenc"])
    else:
        cmd.extend(["-map", "[v]", "-map", "[a]"])

    cmd.extend(["-c:a", "aac", "-b:a", "96k", DUB_VIDEO])
    subprocess.run(cmd, check=True)
    rprint(f"[bold green]Video and audio successfully merged into {DUB_VIDEO}[/bold green]")


if __name__ == "__main__":
    merge_video_audio()
