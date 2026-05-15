#!/usr/bin/env python3
"""
【模块】asr_en_srt.py — 无 YouTube 字幕时：从视频抽音、faster-whisper 英文识别、输出 .en.srt。
【调用方】命令行；run.py 在 --asr-whisper 且无 VTT 时调用。

无 YouTube 字幕时：从视频抽音频 → faster-whisper 识别英文 → 写出 .en.srt。
需: ffmpeg（PATH 或 pip 的 static-ffmpeg）、faster-whisper（见 requirements-pro.txt）
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import pysrt
except ImportError:
    print("请先安装: pip install pysrt", file=sys.stderr)
    sys.exit(1)


def _resolve_ffmpeg() -> str:
    """优先 PATH；否则 static-ffmpeg 注入后再查；最后尝试 imageio-ffmpeg。"""
    import shutil

    try:
        import static_ffmpeg

        static_ffmpeg.add_paths()
    except ImportError:
        pass
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    print(
        "缺少 ffmpeg。请任选其一：\n"
        "  • 安装 FFmpeg 并加入 PATH；或\n"
        "  • pip install -r requirements-pro.txt（含 static-ffmpeg）\n",
        file=sys.stderr,
    )
    sys.exit(2)


def extract_wav_16k_mono(video: Path, wav_out: Path, *, ffmpeg: str) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav_out),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def transcribe_to_srt(
    wav: Path,
    out_srt: Path,
    *,
    model_name: str,
    device: str,
    compute_type: str,
    language: str | None,
) -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        print(
            "未安装 faster-whisper。请执行: pip install -r requirements-pro.txt",
            file=sys.stderr,
        )
        raise SystemExit(3) from e

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(
        str(wav),
        language=language,
        vad_filter=True,
        beam_size=5,
    )
    out = pysrt.SubRipFile()
    idx = 1
    for seg in segments:
        text = re.sub(r"\s+", " ", (seg.text or "").strip())
        if not text:
            continue
        st = pysrt.SubRipTime(milliseconds=int(seg.start * 1000))
        et = pysrt.SubRipTime(milliseconds=int(seg.end * 1000))
        out.append(pysrt.SubRipItem(idx, st, et, text))
        idx += 1
    if len(out) == 0:
        print("识别结果为空。", file=sys.stderr)
        sys.exit(4)
    out_srt.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(out_srt), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="faster-whisper 生成英文字幕 SRT")
    ap.add_argument("video", type=Path, help="输入视频")
    ap.add_argument("out_srt", type=Path, help="输出 .en.srt")
    ap.add_argument("--model", default="small", help="Whisper 模型名，如 small / medium / large-v3")
    ap.add_argument(
        "--device",
        default="cpu",
        help="推理设备：cpu（默认，最稳）/ cuda / auto（auto 在无完整 CUDA 时可能报错）",
    )
    ap.add_argument("--compute-type", default="default")
    ap.add_argument("--language", default="en", help="固定语言，如 en；留空则自动检测")
    args = ap.parse_args()

    ffmpeg = _resolve_ffmpeg()

    with tempfile.TemporaryDirectory(prefix="ytasr_") as td:
        wav = Path(td) / "a.wav"
        extract_wav_16k_mono(args.video, wav, ffmpeg=ffmpeg)
        lang = args.language.strip() or None
        transcribe_to_srt(
            wav,
            args.out_srt,
            model_name=args.model,
            device=args.device,
            compute_type=args.compute_type,
            language=lang,
        )
    print(str(args.out_srt.resolve()))


if __name__ == "__main__":
    main()
