"""
Demucs 人声分离。兼容 PyPI demucs 4.x（无 demucs.api 时用 apply_model）。
"""

from __future__ import annotations

import gc
import os

import torch
import torchaudio
from demucs.apply import apply_model
from demucs.audio import save_audio
from demucs.pretrained import get_model
from rich import print as rprint
from rich.console import Console
from torch.cuda import is_available as is_cuda_available

from core.utils.models import _AUDIO_DIR, _BACKGROUND_AUDIO_FILE, _RAW_AUDIO_FILE, _VOCAL_AUDIO_FILE


def _device():
    if is_cuda_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def demucs_audio():
    if os.path.exists(_VOCAL_AUDIO_FILE) and os.path.exists(_BACKGROUND_AUDIO_FILE):
        rprint(
            f"[yellow]⚠️ {_VOCAL_AUDIO_FILE} and {_BACKGROUND_AUDIO_FILE} "
            "already exist, skip Demucs processing.[/yellow]"
        )
        return

    console = Console()
    os.makedirs(_AUDIO_DIR, exist_ok=True)

    console.print("🤖 Loading <htdemucs> model...")
    model = get_model("htdemucs")
    device = _device()

    console.print("🎵 Separating audio...")
    wav, sr = torchaudio.load(_RAW_AUDIO_FILE)
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    elif wav.shape[0] > 2:
        wav = wav[:2]
    if sr != model.samplerate:
        wav = torchaudio.functional.resample(wav, sr, model.samplerate)
        sr = model.samplerate

    with torch.inference_mode():
        sources = apply_model(
            model,
            wav[None],
            device=device,
            shifts=1,
            split=True,
            overlap=0.25,
            progress=True,
        )[0]

    kwargs = {
        "samplerate": model.samplerate,
        "bitrate": 128,
        "preset": 2,
        "clip": "rescale",
        "as_float": False,
        "bits_per_sample": 16,
    }

    source_names = model.sources
    outputs = {name: sources[i] for i, name in enumerate(source_names)}

    console.print("🎤 Saving vocals track...")
    save_audio(outputs["vocals"].cpu(), _VOCAL_AUDIO_FILE, **kwargs)

    console.print("🎹 Saving background music...")
    background = sum(
        audio for name, audio in outputs.items() if name != "vocals"
    )
    save_audio(background.cpu(), _BACKGROUND_AUDIO_FILE, **kwargs)

    del outputs, background, model, wav, sources
    gc.collect()

    console.print("[green]✨ Audio separation completed![/green]")


if __name__ == "__main__":
    demucs_audio()
