#!/usr/bin/env python3
"""
【模块】mix_dub_background.py — 成片音轨：保留原片声音 + 中文配音。

默认模式 **duck**（推荐，无需 Demucs）：
- 不静音原片，完整保留环境声 / BGM；
- 按英文字幕时间轴判定「原片有人在说话」的时段，将该时段原声音量压低；
- 再与中文 TTS 混音（原声非说话段可略抬高，听感更自然）。

可选模式 **demucs**（YOUTOBE_DUB_BG_MODE=demucs）：
- Demucs two-stems → no_vocals + 配音（慢、依赖 torch，见 requirements-dub-background.txt）。
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ffmpeg volume=eval 嵌套 if 过长会失败；超过此条数口播窗口时改用 pydub
_FFMPEG_DUCK_MAX_WINDOWS = int(os.getenv("YOUTOBE_DUB_DUCK_FFMPEG_MAX_WINDOWS", "40") or "40")


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stderr or "") + (p.stdout or "")


def _which(name: str) -> str | None:
    return shutil.which(name)


def _env_float(name: str, default: float) -> float:
    try:
        v = float((os.getenv(name, "") or "").strip() or default)
        return v if v == v else default
    except ValueError:
        return default


def _bg_mode() -> str:
    return (os.getenv("YOUTOBE_DUB_BG_MODE", "duck").strip().lower() or "duck")


def _probe_duration_sec(path: Path, ffprobe: str) -> float:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    code, out = _run(cmd)
    if code != 0:
        return 0.0
    try:
        return max(0.01, float((out or "").strip().splitlines()[-1]))
    except ValueError:
        return 0.0


def _video_has_audio(video: Path, ffprobe: str) -> bool:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(video),
    ]
    code, out = _run(cmd)
    if code != 0:
        return False
    return bool((out or "").strip())


def _merge_time_windows(
    windows: list[tuple[float, float]],
    *,
    merge_gap_sec: float = 0.0,
) -> list[tuple[float, float]]:
    """
    仅合并时间上有重叠的窗口（pad 后相邻 cue 可能重叠）。
    不用「间隙 < N 秒就合并」，否则口播密集时会把整片合成一段，句间环境声无法抬高。
    """
    if not windows:
        return []
    windows = sorted((max(0.0, s), max(s, e)) for s, e in windows)
    out: list[tuple[float, float]] = [windows[0]]
    for s, e in windows[1:]:
        ps, pe = out[-1]
        if s <= pe + max(0.0, merge_gap_sec):
            out[-1] = (ps, max(pe, e))
        else:
            out.append((s, e))
    return out


def speech_windows_from_en_srt(
    en_srt: Path,
    *,
    pad_sec: float,
    max_duration: float | None = None,
) -> list[tuple[float, float]]:
    import pysrt

    subs = pysrt.open(str(en_srt), encoding="utf-8")
    wins: list[tuple[float, float]] = []
    for sub in subs:
        text = (sub.text or "").replace("\n", " ").strip()
        if not text:
            continue
        st = sub.start.ordinal / 1000.0
        en = sub.end.ordinal / 1000.0
        pad = min(max(0.0, pad_sec), 0.04)
        wins.append((max(0.0, st - pad), max(st + 0.02, en + pad)))
    # 仅合并字幕本身重叠的区间；不按间隙合并，避免整片被当成一个口播段
    wins = _merge_time_windows(wins, merge_gap_sec=0.0)
    if max_duration is not None and max_duration > 0:
        cap = float(max_duration)
        wins = [(s, min(e, cap)) for s, e in wins if s < cap]
        wins = [(s, e) for s, e in wins if e > s]
    return wins


def _ffmpeg_volume_expr(
    windows: list[tuple[float, float]],
    *,
    bg_level: float,
    duck_level: float,
) -> str:
    """原声动态音量：口播时段 duck_level，其余 bg_level。"""
    bg = max(0.0, min(bg_level, 2.0))
    duck = max(0.0, min(duck_level, 2.0))
    expr = f"{bg:.6f}"
    for s, e in reversed(windows):
        expr = f"if(between(t,{s:.3f},{e:.3f}),{duck:.6f},{expr})"
    return expr


def _level_to_db(level: float, *, floor: float = 1e-6) -> float:
    return 20.0 * math.log10(max(float(level), floor))


def mix_dub_duck_pydub(
    video: Path,
    dub: Path,
    out_audio: Path,
    *,
    windows: list[tuple[float, float]],
    ffmpeg: str,
    ffprobe: str,
    bg_level: float,
    duck_level: float,
    dub_volume: float,
    dub_dur: float,
    tmp_dir: Path,
) -> None:
    """长视频多字幕段：pydub 分段 duck，避免 ffmpeg volume 表达式过长。"""
    from pydub import AudioSegment

    orig_wav = tmp_dir / "orig_duck_src.wav"
    extract_stereo_wav(video, orig_wav, ffmpeg=ffmpeg, sample_rate=48000)
    orig = AudioSegment.from_file(str(orig_wav))
    if orig.channels == 1:
        orig = orig.set_channels(2)
    elif orig.channels > 2:
        orig = orig.split_to_mono()[0].set_channels(2)

    target_ms = max(1, int(dub_dur * 1000))
    if len(orig) < target_ms:
        orig = orig + AudioSegment.silent(duration=target_ms - len(orig))
    else:
        orig = orig[:target_ms]

    bg_db = _level_to_db(bg_level)
    duck_extra_db = _level_to_db(duck_level / max(bg_level, 1e-6))
    audio = orig.apply_gain(bg_db)
    for s, e in windows:
        s_ms = max(0, int(s * 1000))
        e_ms = min(len(audio), int(e * 1000))
        if e_ms <= s_ms + 5:
            continue
        seg = audio[s_ms:e_ms].apply_gain(duck_extra_db)
        audio = audio[:s_ms] + seg + audio[e_ms:]

    dub_seg = AudioSegment.from_file(str(dub))
    if dub_seg.channels == 1:
        dub_seg = dub_seg.set_channels(2)
    elif dub_seg.channels > 2:
        dub_seg = dub_seg.split_to_mono()[0].set_channels(2)
    if dub_volume != 1.0:
        dub_seg = dub_seg.apply_gain(_level_to_db(dub_volume))
    if len(dub_seg) < target_ms:
        dub_seg = dub_seg + AudioSegment.silent(duration=target_ms - len(dub_seg))
    else:
        dub_seg = dub_seg[:target_ms]

    mixed = audio.overlay(dub_seg)
    mix_wav = tmp_dir / "duck_mix.wav"
    mixed.export(str(mix_wav), format="wav")
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    code, err = _run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            str(mix_wav),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            str(out_audio),
        ]
    )
    if code != 0:
        raise RuntimeError(err or "ffmpeg 编码 duck 混音失败")


def mix_dub_duck_ffmpeg(
    video: Path,
    dub: Path,
    out_audio: Path,
    *,
    windows: list[tuple[float, float]],
    ffmpeg: str,
    ffprobe: str,
    bg_level: float,
    duck_level: float,
    dub_volume: float,
    dub_dur: float,
) -> None:
    vol_expr = (
        _ffmpeg_volume_expr(windows, bg_level=bg_level, duck_level=duck_level)
        if windows
        else f"{max(0.0, min(bg_level, 2.0)):.6f}"
    )
    filt = (
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo,"
        f"atrim=duration={dub_dur:.6f},asetpts=PTS-STARTPTS,"
        f"apad=whole_dur={dub_dur:.6f},"
        f"volume=volume='{vol_expr}':eval=frame[orig];"
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,"
        f"atrim=duration={dub_dur:.6f},asetpts=PTS-STARTPTS,"
        f"volume={dub_volume:.4f}[dv];"
        f"[orig][dv]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[aout]"
    )
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    code, err = _run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            str(video),
            "-i",
            str(dub),
            "-filter_complex",
            filt,
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            str(out_audio),
        ]
    )
    if code != 0:
        raise RuntimeError(err or "ffmpeg duck 混音失败")


def mix_dub_duck_original(
    video: Path,
    dub: Path,
    out_audio: Path,
    *,
    en_srt: Path | None,
    ffmpeg: str,
    ffprobe: str,
    bg_level: float,
    duck_level: float,
    dub_volume: float,
    pad_sec: float,
) -> None:
    dub_dur = _probe_duration_sec(dub, ffprobe)
    if dub_dur <= 0:
        dub_dur = _probe_duration_sec(video, ffprobe)

    windows: list[tuple[float, float]] = []
    if en_srt and en_srt.is_file():
        windows = speech_windows_from_en_srt(
            en_srt, pad_sec=pad_sec, max_duration=dub_dur + 1.0
        )
        print(
            f"原声 duck: 按 {en_srt.name} 共 {len(windows)} 个口播时段 "
            f"（非口播≈{bg_level:.0%} 音量，口播≈{duck_level:.0%}）+ 中文配音 …",
            file=sys.stderr,
        )
    else:
        print(
            f"原声 duck: 未提供英文字幕，原声统一 {bg_level:.0%} + 中文配音 …",
            file=sys.stderr,
        )

    use_pydub = len(windows) > _FFMPEG_DUCK_MAX_WINDOWS
    if use_pydub:
        print(
            f"原声 duck: 口播窗口较多（>{_FFMPEG_DUCK_MAX_WINDOWS}），使用 pydub 分段处理 …",
            file=sys.stderr,
        )
    tmp = Path(tempfile.mkdtemp(prefix="ytduck_"))
    try:
        if use_pydub:
            mix_dub_duck_pydub(
                video,
                dub,
                out_audio,
                windows=windows,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                bg_level=bg_level,
                duck_level=duck_level,
                dub_volume=dub_volume,
                dub_dur=dub_dur,
                tmp_dir=tmp,
            )
        else:
            try:
                mix_dub_duck_ffmpeg(
                    video,
                    dub,
                    out_audio,
                    windows=windows,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    bg_level=bg_level,
                    duck_level=duck_level,
                    dub_volume=dub_volume,
                    dub_dur=dub_dur,
                )
            except RuntimeError:
                print(
                    "原声 duck: ffmpeg 表达式失败，改用 pydub 分段处理 …",
                    file=sys.stderr,
                )
                mix_dub_duck_pydub(
                    video,
                    dub,
                    out_audio,
                    windows=windows,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    bg_level=bg_level,
                    duck_level=duck_level,
                    dub_volume=dub_volume,
                    dub_dur=dub_dur,
                    tmp_dir=tmp,
                )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def extract_stereo_wav(
    video: Path,
    out_wav: Path,
    *,
    ffmpeg: str,
    sample_rate: int = 44100,
) -> None:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-vn",
        "-map",
        "0:a:0?",
        "-ac",
        "2",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]
    code, err = _run(cmd)
    if code != 0 or not out_wav.exists() or out_wav.stat().st_size < 256:
        raise RuntimeError(f"抽取原音失败: {err[:800]}")


def find_no_vocals(demucs_out: Path) -> Path | None:
    for pat in ("no_vocals.wav", "no_vocals.mp3"):
        hits = sorted(demucs_out.rglob(pat))
        if hits:
            return hits[0]
    return None


def _demucs_available(python_exe: str) -> bool:
    r = subprocess.run(
        [python_exe, "-c", "import demucs"],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def run_demucs(
    in_wav: Path,
    out_root: Path,
    *,
    model: str,
    shifts: int,
    segment: str | None,
    python_exe: str,
) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        python_exe,
        "-m",
        "demucs",
        "-n",
        model,
        "--two-stems",
        "vocals",
        "--mp3",
        "-o",
        str(out_root),
    ]
    if segment:
        cmd.extend(["--segment", segment])
    if shifts > 0:
        cmd.extend(["--shifts", str(shifts)])
    cmd.append(str(in_wav))
    print(f"Demucs 分离（模型 {model}，可能较慢）…", file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        err_tail = (p.stderr or "") + "\n" + (p.stdout or "")
        for line in reversed(err_tail.splitlines()):
            s = line.strip()
            if s.startswith(("Error", "Traceback", "ImportError", "OSError", "ModuleNotFound")):
                err_tail = s
                break
        raise RuntimeError(
            (err_tail or "demucs 失败")[:2000]
            + "\n提示: pip install -r requirements-dub-background.txt"
        )
    nv = find_no_vocals(out_root)
    if not nv or not nv.is_file():
        raise RuntimeError(f"未找到 no_vocals，Demucs 输出目录: {out_root}")
    return nv


def mix_no_vocals_with_dub(
    no_vocals: Path,
    dub: Path,
    out_audio: Path,
    *,
    ffmpeg: str,
    ffprobe: str,
    bg_volume: float,
    dub_volume: float,
) -> None:
    dub_dur = _probe_duration_sec(dub, ffprobe)
    if dub_dur <= 0:
        dub_dur = _probe_duration_sec(no_vocals, ffprobe)
    filt = (
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo,"
        f"atrim=duration={dub_dur:.6f},asetpts=PTS-STARTPTS,"
        f"apad=whole_dur={dub_dur:.6f},volume={bg_volume:.4f}[bg];"
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,"
        f"atrim=duration={dub_dur:.6f},asetpts=PTS-STARTPTS,"
        f"volume={dub_volume:.4f}[dv];"
        f"[bg][dv]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[aout]"
    )
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        str(no_vocals),
        "-i",
        str(dub),
        "-filter_complex",
        filt,
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        str(out_audio),
    ]
    code, err = _run(cmd)
    if code != 0:
        raise RuntimeError(err or "ffmpeg 混音失败")


def mix_dub_demucs(
    video: Path,
    dub: Path,
    out: Path,
    *,
    workdir: Path | None,
    ffmpeg: str,
    ffprobe: str,
    python_exe: str | None,
    demucs_model: str | None,
    demucs_shifts: int | None,
    demucs_segment: str | None,
    bg_volume: float | None,
    dub_volume: float | None,
    reuse_stem: Path | None,
) -> None:
    model = (demucs_model or os.getenv("YOUTOBE_DUB_DEMUCS_MODEL", "htdemucs_ft")).strip()
    shifts = demucs_shifts
    if shifts is None:
        try:
            shifts = int(os.getenv("YOUTOBE_DUB_DEMUCS_SHIFTS", "1").strip() or "1")
        except ValueError:
            shifts = 1
    shifts = max(0, min(shifts, 5))
    seg = (
        demucs_segment
        if demucs_segment is not None
        else os.getenv("YOUTOBE_DUB_DEMUCS_SEGMENT", "").strip() or None
    )
    bg_v = bg_volume if bg_volume is not None else _env_float("YOUTOBE_DUB_BG_MIX_VOLUME", 0.92)
    du_v = dub_volume if dub_volume is not None else _env_float("YOUTOBE_DUB_VO_MIX_VOLUME", 1.0)

    py = python_exe or sys.executable
    if reuse_stem is None and not _demucs_available(py):
        raise RuntimeError("未安装 demucs，请 pip install -r requirements-dub-background.txt")

    tmp = workdir or Path(tempfile.mkdtemp(prefix="ytbgmix_"))
    own_tmp = workdir is None
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        orig = tmp / "orig_extract.wav"
        extract_stereo_wav(video, orig, ffmpeg=ffmpeg)
        if reuse_stem and reuse_stem.is_file():
            nv = reuse_stem
            print(f"复用已有 no_vocals: {nv}", file=sys.stderr)
        else:
            demucs_root = tmp / "demucs_out"
            if demucs_root.exists():
                shutil.rmtree(demucs_root, ignore_errors=True)
            nv = run_demucs(
                orig,
                demucs_root,
                model=model,
                shifts=shifts,
                segment=seg,
                python_exe=py,
            )
        mix_no_vocals_with_dub(
            nv,
            dub,
            out,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            bg_volume=bg_v,
            dub_volume=du_v,
        )
    finally:
        if own_tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def mix_dub_with_background(
    video: Path,
    dub: Path,
    out: Path,
    *,
    en_srt: Path | None = None,
    workdir: Path | None = None,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    python_exe: str | None = None,
    mode: str | None = None,
    demucs_model: str | None = None,
    demucs_shifts: int | None = None,
    demucs_segment: str | None = None,
    bg_volume: float | None = None,
    dub_volume: float | None = None,
    duck_bg_volume: float | None = None,
    duck_speech_volume: float | None = None,
    duck_pad_sec: float | None = None,
    reuse_stem: Path | None = None,
) -> None:
    if not _video_has_audio(video, ffprobe):
        print("提示: 原视频无音轨，直接复制配音为成片用音轨。", file=sys.stderr)
        shutil.copy2(dub, out)
        return

    m = (mode or _bg_mode()).strip().lower()
    du_v = dub_volume if dub_volume is not None else _env_float("YOUTOBE_DUB_VO_MIX_VOLUME", 1.0)

    try:
        if m == "demucs":
            mix_dub_demucs(
                video,
                dub,
                out,
                workdir=workdir,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                python_exe=python_exe,
                demucs_model=demucs_model,
                demucs_shifts=demucs_shifts,
                demucs_segment=demucs_segment,
                bg_volume=bg_volume,
                dub_volume=du_v,
                reuse_stem=reuse_stem,
            )
        else:
            if m not in ("duck", "original", "keep"):
                print(f"未知 YOUTOBE_DUB_BG_MODE={m!r}，已按 duck 处理。", file=sys.stderr)
            pad = duck_pad_sec
            if pad is None:
                pad = _env_float("YOUTOBE_DUB_DUCK_PAD_MS", 120.0) / 1000.0
            bg = duck_bg_volume
            if bg is None:
                bg = _env_float("YOUTOBE_DUB_DUCK_BG_VOLUME", 0.52)
            duck = duck_speech_volume
            if duck is None:
                duck = _env_float("YOUTOBE_DUB_DUCK_SPEECH_VOLUME", 0.16)
            mix_dub_duck_original(
                video,
                dub,
                out,
                en_srt=en_srt,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                bg_level=bg,
                duck_level=duck,
                dub_volume=du_v,
                pad_sec=pad,
            )
    except Exception as e:
        print(f"警告: 背景混音失败（{e!s}），已退回仅中文配音轨。", file=sys.stderr)
        shutil.copy2(dub, out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="原片音轨 + 中文配音混音（默认 duck：保留原声并在口播时段压低）"
    )
    ap.add_argument("video", type=Path, help="原视频 MP4")
    ap.add_argument("dub", type=Path, help="中文配音 m4a/wav")
    ap.add_argument("out", type=Path, help="输出混音 m4a")
    ap.add_argument(
        "--en-srt",
        type=Path,
        default=None,
        help="英文字幕 SRT（duck 模式用于判定原片口播时段）",
    )
    ap.add_argument(
        "--mode",
        choices=("duck", "demucs"),
        default=None,
        help="混音模式（默认读 YOUTOBE_DUB_BG_MODE，推荐 duck）",
    )
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    ap.add_argument("--python", dest="python_exe", default=None)
    ap.add_argument("--demucs-model", default=None)
    ap.add_argument("--demucs-shifts", type=int, default=None)
    ap.add_argument("--demucs-segment", default=None)
    ap.add_argument("--bg-volume", type=float, default=None, help="demucs 模式：no_vocals 音量")
    ap.add_argument("--dub-volume", type=float, default=None)
    ap.add_argument("--duck-bg-volume", type=float, default=None, help="duck：非口播时段原声音量 0–1")
    ap.add_argument("--duck-speech-volume", type=float, default=None, help="duck：口播时段原声音量 0–1")
    ap.add_argument("--duck-pad-ms", type=float, default=None, help="duck：口播窗口前后扩展（毫秒）")
    ap.add_argument("--reuse-no-vocals", type=Path, default=None, help="demucs：跳过分离")
    args = ap.parse_args()
    if not _which(args.ffmpeg):
        print("未找到 ffmpeg", file=sys.stderr)
        sys.exit(2)
    if not _which(args.ffprobe):
        print("未找到 ffprobe", file=sys.stderr)
        sys.exit(2)
    pad_sec = None
    if args.duck_pad_ms is not None:
        pad_sec = float(args.duck_pad_ms) / 1000.0
    mix_dub_with_background(
        args.video,
        args.dub,
        args.out,
        en_srt=args.en_srt,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        python_exe=args.python_exe,
        mode=args.mode,
        demucs_model=args.demucs_model,
        demucs_shifts=args.demucs_shifts,
        demucs_segment=args.demucs_segment,
        bg_volume=args.bg_volume,
        dub_volume=args.dub_volume,
        duck_bg_volume=args.duck_bg_volume,
        duck_speech_volume=args.duck_speech_volume,
        duck_pad_sec=pad_sec,
        reuse_stem=args.reuse_no_vocals,
    )
    print(str(args.out.resolve()))


if __name__ == "__main__":
    main()
