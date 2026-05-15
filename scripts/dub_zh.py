#!/usr/bin/env python3
"""
根据中文 SRT 生成与画面时长对齐的中文配音音轨，导出 WAV/M4A。

- **--sync-en-time**（默认开）：若同目录存在 `{stem}.en.srt`，则配音起止时间与断句**与英文字幕一致**，
  与 `merge_bilingual_srt.py` 硬烧双语字幕对齐，解决「中文配音还在读、画面已切下一句」的错位。
- **--duration-fit-openai**（默认开，需 OPENAI_API_KEY）：按每条英文字幕的**时长**压缩/改写中文口播，
  使中文 TTS 大致落在该时段内，减轻变速与截断。
- --backend edge / volc / **elevenlabs** / **auto**（auto：火山 Key > ElevenLabs Key > Edge，见 env.example）
- 使用英文时间轴时**不再**做相邻句合并（避免破坏原片停顿）；纯中文时间轴时仍可用 **--merge-repeats**。
- **--max-speedup**：限制为对齐英文槽位时的最大加速倍数（默认约 1.22；超出则裁尾）。
  优先用 **ffmpeg atempo** 做时长压缩（比 pydub 更自然）；可用 `YOUTOBE_DUB_FFMPEG_ATEMPO=0` 关闭。
- Edge：**--edge-rate** 略放慢（默认 -5%%）+ 默认音色 **zh-CN-YunxiNeural**
- **--colloquial-openai** / **--tts-polish-openai**：口播化与 TTS 标点润色
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from difflib import SequenceMatcher
from pathlib import Path

try:
    import edge_tts
except ImportError:
    edge_tts = None  # type: ignore

try:
    import pysrt
except ImportError:
    print("请先安装: pip install pysrt", file=sys.stderr)
    sys.exit(1)

try:
    from pydub import AudioSegment
except ImportError as e:
    extra = ""
    em = str(e).lower()
    if "audioop" in em or "pyaudioop" in em:
        extra = (
            "\nPython 3.13+ 需额外: pip install audioop-lts\n"
            "或: pip install -r requirements.txt\n"
        )
    print(
        "缺少 pydub 或其依赖。请在项目根执行: pip install pydub\n"
        "或: pip install -r requirements.txt\n"
        "（建议使用项目内 .venv 的 python）\n"
        f"原始错误: {e}\n"
        f"{extra}",
        file=sys.stderr,
    )
    sys.exit(1)

EDGE_DEFAULT_VOICE = "zh-CN-YunxiNeural"


def _float_env(name: str, default: float) -> float:
    try:
        v = os.getenv(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def _edge_tts_proxy() -> str | None:
    """Edge TTS 走 WebSocket，国内常需代理，否则易 NoAudioReceived。"""
    for k in ("YOUTOBE_EDGE_TTS_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        v = os.getenv(k, "").strip()
        if v:
            return v
    return None


def _sanitize_tts_text(s: str) -> str:
    """去掉易让 TTS 读坏的符号，减轻 Edge 断句怪异。"""
    t = (s or "").replace("\n", " ").replace("…", "，").replace("⋯", "，")
    t = t.replace("「", "").replace("」", "").replace("『", "").replace("』", "")
    t = re.sub(r"[#*_`]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _bootstrap_ffmpeg_path() -> None:
    """无系统 FFmpeg 时，用 pip 的 static-ffmpeg 注入 PATH（含 ffprobe，供 pydub 使用）。"""
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths()
    except ImportError:
        pass


def _ensure_ffmpeg_tools() -> None:
    _bootstrap_ffmpeg_path()
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        print(
            "错误: 未找到 ffmpeg / ffprobe。\n"
            "请安装 FFmpeg 并加入 PATH，或执行: pip install -r requirements-pro.txt（含 static-ffmpeg）",
            file=sys.stderr,
        )
        sys.exit(2)


def _video_duration_sec(video: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return max(float(out), 0.1)


def _duration_fallback_from_subs(subs: list) -> float:
    if not subs:
        return 120.0
    last = subs[-1].end
    return max(_time_to_ms(last) / 1000.0 + 2.0, 1.0)


def _time_to_ms(t: pysrt.SubRipTime) -> int:
    return (
        t.hours * 3600000
        + t.minutes * 60000
        + t.seconds * 1000
        + t.milliseconds
    )


def _ms_to_subrip_time(ms: int) -> pysrt.SubRipTime:
    if ms < 0:
        ms = 0
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    mil = ms % 1000
    return pysrt.SubRipTime(hours=h, minutes=m, seconds=s, milliseconds=mil)


def _write_zh_dubsync_srt(segments: list[tuple[int, int, str]], out_path: Path) -> None:
    """与英文时间轴一致的中文字幕，中文行与最终口播稿一致。"""
    out = pysrt.SubRipFile()
    n = 0
    for sm, em, zt in segments:
        n += 1
        out.append(
            pysrt.SubRipItem(
                n,
                _ms_to_subrip_time(sm),
                _ms_to_subrip_time(em),
                (zt or "").strip(),
            )
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(out_path), encoding="utf-8")


def _zh_dubsync_path(zh_srt: Path) -> Path:
    name = zh_srt.name
    if name.endswith(".zh.srt"):
        stem = name[: -len(".zh.srt")]
    else:
        stem = zh_srt.stem
    return zh_srt.parent / f"{stem}.zh.dubsync.srt"


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def _line_similarity(a: str, b: str) -> float:
    ca, cb = _compact(a), _compact(b)
    if not ca or not cb:
        return 0.0
    return SequenceMatcher(None, ca, cb).ratio()


def _similar_lines(a: str, b: str, thr: float = 0.82) -> bool:
    if a.strip() == b.strip():
        return True
    ca, cb = _compact(a), _compact(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    if len(ca) >= 3 and (cb in ca or ca in cb):
        return _line_similarity(a, b) >= 0.45
    return _line_similarity(a, b) >= thr


def _collapse_parts(parts: list[str]) -> str:
    if not parts:
        return ""
    out = [parts[0].strip()]
    for p in parts[1:]:
        pt = p.strip()
        if not pt:
            continue
        if _similar_lines(pt, out[-1], 0.88):
            continue
        out.append(pt)
    if len(out) == 1:
        return out[0]
    return "，".join(out)


_MAX_DUB_SLOT_MS = 50_000
_MAX_DUB_TEXT_CHARS = 480
_MAX_GAP_BREAK_MS = 2_200


def _rows_from_subs(subs: list) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    for sub in subs:
        t = sub.text.replace("\n", " ").strip()
        if not t:
            continue
        rows.append((_time_to_ms(sub.start), _time_to_ms(sub.end), t))
    return rows


def _default_en_srt_path(zh_srt: Path) -> Path | None:
    """如 `foo.zh.srt` → `foo.en.srt`。"""
    name = zh_srt.name
    if not name.endswith(".zh.srt"):
        return None
    cand = zh_srt.with_name(name.replace(".zh.srt", ".en.srt", 1))
    return cand if cand.exists() else None


def _rows_en_zh_sync(
    en_srt: Path, zh_srt: Path
) -> tuple[list[tuple[int, int, str]], list[str]]:
    """每条用**英文**起止时间 + 同索引中文文本；与双语硬烧字幕时间轴一致。"""
    en_sub = list(pysrt.open(str(en_srt)))
    zh_sub = list(pysrt.open(str(zh_srt)))
    n_en, n_zh = len(en_sub), len(zh_sub)
    if n_zh < n_en:
        print(
            f"警告: 中文 {n_zh} 条 < 英文 {n_en} 条，缺行处该时段无中文配音。",
            file=sys.stderr,
        )
    elif n_zh > n_en:
        print(
            f"警告: 中文 {n_zh} 条 > 英文 {n_en} 条，仅使用前 {n_en} 条与英文对齐。",
            file=sys.stderr,
        )
    rows: list[tuple[int, int, str]] = []
    en_lines: list[str] = []
    for i, e in enumerate(en_sub):
        sm, em = _time_to_ms(e.start), _time_to_ms(e.end)
        zt = zh_sub[i].text.replace("\n", " ").strip() if i < n_zh else ""
        et = e.text.replace("\n", " ").strip()
        rows.append((sm, em, zt))
        en_lines.append(et)
    return rows, en_lines


def _last_end_ms_from_subs(subs: list) -> int:
    if not subs:
        return 0
    return _time_to_ms(subs[-1].end)


def _duration_fit_segments_en(
    segments: list[tuple[int, int, str]],
    en_lines: list[str],
    *,
    api_key: str,
    chars_per_sec_target: float,
) -> list[tuple[int, int, str]]:
    from translation_clients import openai_dub_duration_fit_batch

    if len(segments) != len(en_lines):
        raise RuntimeError("英文句与中文段数量不一致")
    out_text = [s[2] for s in segments]
    chunk = 10
    buf: list[tuple[str, str, float]] = []
    buf_i: list[int] = []
    n_api = 0

    def flush() -> None:
        nonlocal buf, buf_i, n_api
        if not buf:
            return
        got = openai_dub_duration_fit_batch(
            buf,
            api_key=api_key,
            chars_per_sec_budget=chars_per_sec_target,
        )
        for k, ii in enumerate(buf_i):
            out_text[ii] = got[k]
        n_api += len(buf)
        buf = []
        buf_i = []
        time.sleep(0.18)

    for i, (sm, em, zh) in enumerate(segments):
        zh = (zh or "").strip()
        en = (en_lines[i] or "").strip()
        slot_sec = max(0.06, (em - sm) / 1000.0)
        if not zh:
            continue
        budget = slot_sec * chars_per_sec_target
        if len(zh) <= max(4, budget * 1.06):
            continue
        buf_i.append(i)
        buf.append((en, zh, slot_sec))
        if len(buf) >= chunk:
            flush()
    flush()
    if n_api:
        print(
            f"时长适配(OpenAI): 已对 {n_api} 条中文口播按英文字幕时长压缩/改写。",
            file=sys.stderr,
        )
    return [(segments[i][0], segments[i][1], out_text[i]) for i in range(len(segments))]


def _merge_dub_rows(
    rows: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    if len(rows) <= 1:
        return list(rows)
    merged: list[tuple[int, int, str]] = []
    cur_s, cur_e, parts = rows[0][0], rows[0][1], [rows[0][2]]
    for sm, em, text in rows[1:]:
        gap = sm - cur_e
        span = em - cur_s
        trial = _collapse_parts(parts + [text])
        sim = _similar_lines(parts[-1], text)
        tiny_overlap = (
            gap <= 450
            and bool(_compact(text))
            and (
                _compact(text) in _compact(parts[-1])
                or _compact(parts[-1]) in _compact(text)
            )
        )
        merge_ok = (
            span <= _MAX_DUB_SLOT_MS
            and len(trial) <= _MAX_DUB_TEXT_CHARS
            and (sim or tiny_overlap)
        )
        if merge_ok and gap > _MAX_GAP_BREAK_MS and not _similar_lines(parts[-1], text, 0.94):
            merge_ok = False
        if merge_ok:
            cur_e = em
            parts.append(text)
        else:
            ft = _collapse_parts(parts).strip()
            if ft and cur_e > cur_s:
                merged.append((cur_s, cur_e, ft))
            cur_s, cur_e, parts = sm, em, [text]
    ft = _collapse_parts(parts).strip()
    if ft and cur_e > cur_s:
        merged.append((cur_s, cur_e, ft))
    return merged


def _colloquialize_segments(
    segments: list[tuple[int, int, str]],
    *,
    api_key: str,
) -> list[tuple[int, int, str]]:
    from translation_clients import openai_colloquial_zh_batch

    texts = [s[2] for s in segments]
    out: list[str] = []
    chunk = 14
    for i in range(0, len(texts), chunk):
        batch = texts[i : i + chunk]
        got = openai_colloquial_zh_batch(batch, api_key=api_key)
        if len(got) != len(batch):
            raise RuntimeError("口语化 API 返回条数与请求不一致")
        out.extend(got)
        time.sleep(0.22)
    return [(segments[j][0], segments[j][1], out[j]) for j in range(len(segments))]


def _tts_polish_segments(
    segments: list[tuple[int, int, str]],
    *,
    api_key: str,
) -> list[tuple[int, int, str]]:
    from translation_clients import openai_tts_polish_batch

    texts = [s[2] for s in segments]
    out: list[str] = []
    chunk = 12
    for i in range(0, len(texts), chunk):
        batch = texts[i : i + chunk]
        got = openai_tts_polish_batch(batch, api_key=api_key)
        if len(got) != len(batch):
            raise RuntimeError("TTS 润色 API 返回条数与请求不一致")
        out.extend(got)
        time.sleep(0.2)
    return [(segments[j][0], segments[j][1], out[j]) for j in range(len(segments))]


def _tts_raw_clip_bad(seg: AudioSegment, text: str) -> bool:
    """
    检测刚写入的 TTS 文件是否明显异常（过短 / 过静）。
    Edge 在网络不稳时偶发极小或静音 MP3，若不重试会导致整条字幕时段几乎全静音。
    """
    t = (text or "").strip()
    if len(t) < 4:
        return False
    if len(seg) < 80:
        return True
    try:
        rms = int(seg.rms)
    except Exception:
        return True
    if rms < 80:
        return True
    if len(t) >= 10 and len(seg) < len(t) * 40:
        return True
    return False


def _ff_audio_atempo_chain(factor: float) -> str:
    """生成 ffmpeg atempo 链，使时长约为原来的 1/factor（factor>1 表示加速）。"""
    parts: list[str] = []
    f = float(factor)
    if f <= 1.0 + 1e-9:
        return "atempo=1.0"
    while f > 2.0 + 1e-9:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        f /= 0.5
    t = f"{f:.6f}".rstrip("0").rstrip(".")
    parts.append(f"atempo={t}" if t else "atempo=1.0")
    return ",".join(parts)


def _try_ffmpeg_atempo_shorten(
    seg: AudioSegment, factor: float, workdir: Path
) -> AudioSegment | None:
    """用 ffmpeg atempo 将音频缩短约 factor 倍（factor>1）。失败返回 None。"""
    ff = shutil.which("ffmpeg")
    if not ff or factor <= 1.001:
        return None
    tok = uuid.uuid4().hex[:12]
    fin = workdir / f"dubatempo_{tok}_in.wav"
    fout = workdir / f"dubatempo_{tok}_out.wav"
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        seg.export(str(fin), format="wav")
        filt = _ff_audio_atempo_chain(factor)
        cmd = [
            ff,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(fin),
            "-filter:a",
            filt,
            str(fout),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not fout.exists() or fout.stat().st_size < 64:
            return None
        return AudioSegment.from_file(str(fout), format="wav")
    except Exception:
        return None
    finally:
        fin.unlink(missing_ok=True)
        fout.unlink(missing_ok=True)


def _fit_segment(
    seg: AudioSegment,
    slot_ms: int,
    *,
    max_speedup: float,
    workdir: Path,
) -> AudioSegment:
    if slot_ms <= 0:
        return AudioSegment.silent(duration=0)
    if len(seg) <= slot_ms:
        return seg + AudioSegment.silent(duration=slot_ms - len(seg))
    ratio = len(seg) / float(slot_ms)
    # 略长于槽位：直接裁尾，保持 TTS 原语速（避免每段变速幅度不一）
    trunc_max = _float_env("YOUTOBE_DUB_TRUNCATE_RATIO", 1.12)
    trunc_max = max(1.0, min(trunc_max, 1.28))
    if ratio <= trunc_max:
        return seg[:slot_ms]
    use_ratio = min(ratio, max(1.01, max_speedup))
    if use_ratio <= trunc_max:
        return seg[:slot_ms]
    use_ff = os.getenv("YOUTOBE_DUB_FFMPEG_ATEMPO", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    seg2: AudioSegment | None = None
    if use_ff and shutil.which("ffmpeg"):
        seg2 = _try_ffmpeg_atempo_shorten(seg, use_ratio, workdir)
        if seg2 is not None and not getattr(_fit_segment, "_atempo_notice", False):
            print(
                "配音对齐: 已启用 ffmpeg atempo 压缩超长句（语调比 pydub speedup 更自然）；"
                "仍超出部分会裁尾。可用 YOUTOBE_DUB_FFMPEG_ATEMPO=0 关闭。",
                file=sys.stderr,
            )
            setattr(_fit_segment, "_atempo_notice", True)
    if seg2 is not None:
        seg = seg2
    else:
        # pydub speedup 会明显改变语调，仅作 ffmpeg 不可用时的后备
        if ratio <= 1.03:
            return seg[:slot_ms]
        use_ratio = min(ratio, max(1.01, max_speedup))
        try:
            seg = seg.speedup(playback_speed=use_ratio)
        except Exception:
            seg = seg._spawn(
                seg.raw_data,
                overrides={"frame_rate": int(seg.frame_rate * use_ratio)},
            ).set_frame_rate(seg.frame_rate)
    if len(seg) > slot_ms:
        seg = seg[:slot_ms]
    if len(seg) < slot_ms:
        seg = seg + AudioSegment.silent(duration=slot_ms - len(seg))
    return seg


async def _tts_edge_save(
    text: str,
    path: Path,
    voice: str,
    *,
    rate: str,
    pitch: str,
    proxy: str | None,
) -> None:
    if edge_tts is None:
        raise RuntimeError("未安装 edge-tts")
    last_err: Exception | None = None
    for net in range(5):
        for q in range(6):
            try:
                if proxy:
                    com = edge_tts.Communicate(
                        text, voice, rate=rate, pitch=pitch, proxy=proxy
                    )
                else:
                    com = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
                await com.save(str(path))
                seg = AudioSegment.from_file(str(path), format="mp3")
                if not _tts_raw_clip_bad(seg, text):
                    return
                print(
                    f"Edge TTS 输出过短或过静 (len={len(seg)}ms, rms≈{seg.rms})，"
                    f"质量重试 {q + 1}/6…",
                    file=sys.stderr,
                )
                await asyncio.sleep(0.35 + 0.18 * q)
            except Exception as e:
                last_err = e
                print(f"Edge TTS 请求异常: {e!s}", file=sys.stderr)
                break
        else:
            last_err = last_err or RuntimeError(
                "Edge TTS 多次合成仍为过短/过静，请检查网络或代理"
            )
        if net < 4:
            wait = 1.2 * (2**min(net, 3))
            print(
                f"Edge TTS 网络轮重试 ({net + 1}/5) 等待 {wait:.1f}s…",
                file=sys.stderr,
            )
            await asyncio.sleep(wait)
    assert last_err is not None
    raise last_err


def _tts_volc_save_validated(
    text: str,
    path: Path,
    voice: str,
    response_format: str,
    sample_rate: int,
) -> None:
    last_err: Exception | None = None
    for net in range(5):
        for q in range(6):
            try:
                path.unlink(missing_ok=True)
                _tts_volc_save(text, path, voice, response_format, sample_rate)
                seg = AudioSegment.from_file(str(path), format=response_format)
                if not _tts_raw_clip_bad(seg, text):
                    return
                print(
                    f"火山 TTS 输出过短或过静 (len={len(seg)}ms)，质量重试 {q + 1}/6…",
                    file=sys.stderr,
                )
                time.sleep(0.35 + 0.18 * q)
            except Exception as e:
                last_err = e
                print(f"火山 TTS 异常: {e!s}", file=sys.stderr)
                break
        else:
            last_err = last_err or RuntimeError("火山 TTS 多次合成仍为过短/过静")
        if net < 4:
            time.sleep(1.2 * (2**min(net, 3)))
    assert last_err is not None
    raise last_err


def _tts_elevenlabs_save_validated(text: str, path: Path, voice_id: str) -> None:
    last_err: Exception | None = None
    for net in range(5):
        for q in range(6):
            try:
                path.unlink(missing_ok=True)
                _tts_elevenlabs_save(text, path, voice_id)
                seg = AudioSegment.from_file(str(path), format="mp3")
                if not _tts_raw_clip_bad(seg, text):
                    return
                print(
                    f"ElevenLabs 输出过短或过静 (len={len(seg)}ms)，质量重试 {q + 1}/6…",
                    file=sys.stderr,
                )
                time.sleep(0.35 + 0.18 * q)
            except Exception as e:
                last_err = e
                print(f"ElevenLabs TTS 异常: {e!s}", file=sys.stderr)
                break
        else:
            last_err = last_err or RuntimeError(
                "ElevenLabs TTS 多次合成仍为过短/过静"
            )
        if net < 4:
            time.sleep(1.2 * (2**min(net, 3)))
    assert last_err is not None
    raise last_err


def _tts_elevenlabs_save(text: str, path: Path, voice_id: str) -> None:
    from elevenlabs_tts_client import synthesize_elevenlabs_tts

    data = synthesize_elevenlabs_tts(text, voice_id=voice_id)
    path.write_bytes(data)


def _tts_volc_save(
    text: str, path: Path, voice: str, response_format: str, sample_rate: int
) -> None:
    from volc_tts_client import synthesize_volc_tts

    data = synthesize_volc_tts(
        text,
        speaker=voice,
        response_format=response_format,
        sample_rate=sample_rate,
    )
    path.write_bytes(data)


def _load_clip(path: Path, backend: str, volc_format: str) -> AudioSegment:
    if backend in ("edge", "elevenlabs"):
        return AudioSegment.from_file(str(path), format="mp3")
    return AudioSegment.from_file(str(path), format=volc_format)


async def _build_async(
    video: Path,
    zh_srt: Path,
    out_audio: Path,
    *,
    voice: str,
    concurrency: int,
    merge_repeats: bool,
    colloquial_openai: bool,
    tts_polish_openai: bool,
    backend: str,
    volc_format: str,
    volc_sample_rate: int,
    max_speedup: float,
    edge_rate: str,
    edge_pitch: str,
    sync_en_time: bool,
    duration_fit_openai: bool,
    chars_per_sec_target: float,
    en_srt_explicit: Path | None,
) -> None:
    _ensure_ffmpeg_tools()
    subs_zh = list(pysrt.open(str(zh_srt)))
    en_path: Path | None = None
    if en_srt_explicit is not None:
        if en_srt_explicit.exists():
            en_path = en_srt_explicit
        else:
            print(f"警告: --en-srt 文件不存在: {en_srt_explicit}", file=sys.stderr)
    elif sync_en_time:
        en_path = _default_en_srt_path(zh_srt)

    en_lines: list[str] | None = None
    use_en_master = bool(en_path and en_path.exists())

    try:
        dur_sec = _video_duration_sec(video)
    except (FileNotFoundError, subprocess.CalledProcessError, OSError, ValueError) as e:
        print(f"警告: ffprobe 读取片长失败 ({e})，改用字幕结束时间估算。", file=sys.stderr)
        if use_en_master:
            en_sub = list(pysrt.open(str(en_path)))
            dur_sec = max(
                _duration_fallback_from_subs(subs_zh),
                _last_end_ms_from_subs(en_sub) / 1000.0 + 1.0,
            )
        else:
            dur_sec = _duration_fallback_from_subs(subs_zh)
    dur_ms = int(dur_sec * 1000) + 400
    base = AudioSegment.silent(duration=dur_ms)

    if use_en_master:
        print(
            "配音时间轴: 与英文字幕对齐（断句/停顿与硬烧双语字幕一致）。",
            file=sys.stderr,
        )
        rows, en_lines = _rows_en_zh_sync(en_path, zh_srt)
        segments = list(rows)
        effective_merge = False
    else:
        rows = _rows_from_subs(subs_zh)
        if sync_en_time and not en_path:
            print(
                "提示: 已请求英文时间轴但未找到同目录 .en.srt，使用中文时间轴。",
                file=sys.stderr,
            )
        effective_merge = merge_repeats
        segments = _merge_dub_rows(rows) if effective_merge else list(rows)
        if effective_merge:
            print(
                f"配音合并: {len(rows)} 条字幕 → {len(segments)} 段配音（相邻重复/相似已合并）",
                file=sys.stderr,
            )

    if not segments:
        print("警告: 无有效配音段，输出静音轨。", file=sys.stderr)
        out_audio.parent.mkdir(parents=True, exist_ok=True)
        silent = AudioSegment.silent(duration=dur_ms)
        suf = out_audio.suffix.lower()
        if suf in (".m4a", ".aac", ".mp4"):
            silent.export(str(out_audio), format="ipod", bitrate="256k")
        else:
            silent.export(str(out_audio), format="wav")
        return

    if colloquial_openai:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            print(
                "警告: --colloquial-openai 需要 OPENAI_API_KEY，已跳过口语化。",
                file=sys.stderr,
            )
        else:
            print("口语化: 使用 OpenAI 优化配音稿…", file=sys.stderr)
            segments = _colloquialize_segments(segments, api_key=key)

    if duration_fit_openai and en_lines is not None:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            print(
                "警告: --duration-fit-openai 需要 OPENAI_API_KEY，已跳过时长适配。",
                file=sys.stderr,
            )
        else:
            print(
                "时长适配: 按英文字幕时长压缩中文口播（OpenAI）…",
                file=sys.stderr,
            )
            segments = _duration_fit_segments_en(
                segments,
                en_lines,
                api_key=key,
                chars_per_sec_target=chars_per_sec_target,
            )

    if tts_polish_openai:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            print(
                "警告: --tts-polish-openai 需要 OPENAI_API_KEY，已跳过 TTS 润色。",
                file=sys.stderr,
            )
        else:
            print("TTS 润色: OpenAI 优化断句与符号…", file=sys.stderr)
            segments = _tts_polish_segments(segments, api_key=key)

    if use_en_master:
        dsp = _zh_dubsync_path(zh_srt)
        _write_zh_dubsync_srt(segments, dsp)
        print(f"已写入与口播对齐的中文字幕: {dsp}", file=sys.stderr)

    print(
        "变速策略: 口播略长于槽位时，若「时长/槽位」≤ "
        f"{_float_env('YOUTOBE_DUB_TRUNCATE_RATIO', 1.12):.2f} 则只裁尾保持原语速；"
        f"否则用 ffmpeg atempo 加速（上限 {max_speedup:.2f}×），仍超长再裁尾。"
        "（YOUTOBE_DUB_FFMPEG_ATEMPO=0 可退回旧算法）",
        file=sys.stderr,
    )

    edge_proxy = _edge_tts_proxy() if backend == "edge" else None
    if backend == "edge":
        extra = f"rate={edge_rate}, pitch={edge_pitch}"
        if edge_proxy:
            print(
                f"配音后端: Edge TTS（{extra}，已启用代理 YOUTOBE_EDGE_TTS_PROXY/HTTPS_PROXY 等）",
                file=sys.stderr,
            )
        else:
            print(
                f"配音后端: Edge TTS（{extra}）",
                file=sys.stderr,
            )
            print(
                "提示: 若出现 NoAudioReceived，多为网络无法访问微软语音服务；"
                "可在 .env 设置 YOUTOBE_EDGE_TTS_PROXY 或 HTTPS_PROXY，或改用火山/ElevenLabs TTS。",
                file=sys.stderr,
            )
    elif backend == "elevenlabs":
        print(
            "配音后端: ElevenLabs（eleven_multilingual_v2，融合 ai-dubbing 思路）",
            file=sys.stderr,
        )
    else:
        print(
            f"配音后端: 火山 OpenSpeech TTS（{volc_format} {volc_sample_rate}Hz）",
            file=sys.stderr,
        )

    tmpdir = Path(tempfile.mkdtemp(prefix="ytdub_"))
    sem = asyncio.Semaphore(max(1, concurrency))
    ext = "mp3" if backend in ("edge", "elevenlabs") else volc_format

    jobs: list[tuple[int, int, int, Path, str]] = []
    for i, (start_ms, end_ms, text) in enumerate(segments):
        raw = _sanitize_tts_text(text)
        if not raw.strip():
            continue
        slot_ms = end_ms - start_ms
        if slot_ms < 250:
            continue
        clip = tmpdir / f"c_{i}.{ext}"
        jobs.append((i, start_ms, slot_ms, clip, raw))

    async def _one_edge(
        idx: int, start_ms: int, slot_ms: int, clip: Path, text: str
    ) -> tuple[int, int, int, Path]:
        async with sem:
            await _tts_edge_save(
                text,
                clip,
                voice,
                rate=edge_rate,
                pitch=edge_pitch,
                proxy=edge_proxy,
            )
        return idx, start_ms, slot_ms, clip

    async def _one_volc(
        idx: int, start_ms: int, slot_ms: int, clip: Path, text: str
    ) -> tuple[int, int, int, Path]:
        async with sem:
            await asyncio.to_thread(
                _tts_volc_save_validated,
                text,
                clip,
                voice,
                volc_format,
                volc_sample_rate,
            )
        return idx, start_ms, slot_ms, clip

    async def _one_eleven(
        idx: int, start_ms: int, slot_ms: int, clip: Path, text: str
    ) -> tuple[int, int, int, Path]:
        async with sem:
            await asyncio.to_thread(
                _tts_elevenlabs_save_validated, text, clip, voice
            )
        return idx, start_ms, slot_ms, clip

    if backend == "edge":
        await asyncio.gather(
            *[_one_edge(i, sm, sl, p, tx) for (i, sm, sl, p, tx) in jobs]
        )
    elif backend == "elevenlabs":
        await asyncio.gather(
            *[_one_eleven(i, sm, sl, p, tx) for (i, sm, sl, p, tx) in jobs]
        )
    else:
        await asyncio.gather(
            *[_one_volc(i, sm, sl, p, tx) for (i, sm, sl, p, tx) in jobs]
        )

    ordered = sorted(jobs, key=lambda x: x[1])
    for _i, start_ms, slot_ms, clip, _tx in ordered:
        seg = _load_clip(clip, backend, volc_format)
        seg = _fit_segment(seg, slot_ms, max_speedup=max_speedup, workdir=tmpdir)
        base = base.overlay(seg, position=start_ms)

    shutil.rmtree(tmpdir, ignore_errors=True)

    if len(base) < dur_ms:
        base = base + AudioSegment.silent(duration=dur_ms - len(base))

    out_audio.parent.mkdir(parents=True, exist_ok=True)
    suf = out_audio.suffix.lower()
    if suf in (".m4a", ".aac", ".mp4"):
        base.export(str(out_audio), format="ipod", bitrate="256k")
    else:
        base.export(str(out_audio), format="wav")


def build_dub_track(
    video: Path,
    zh_srt: Path,
    out_audio: Path,
    *,
    voice: str = EDGE_DEFAULT_VOICE,
    concurrency: int = 5,
    merge_repeats: bool = True,
    colloquial_openai: bool = False,
    tts_polish_openai: bool = False,
    backend: str = "edge",
    volc_format: str = "wav",
    volc_sample_rate: int = 24000,
    max_speedup: float | None = None,
    edge_rate: str | None = None,
    edge_pitch: str = "+0Hz",
    sync_en_time: bool = True,
    duration_fit_openai: bool = True,
    chars_per_sec_target: float | None = None,
    en_srt: Path | None = None,
) -> None:
    ms = max_speedup if max_speedup is not None else _float_env("YOUTOBE_DUB_MAX_SPEEDUP", 1.22)
    ms = max(1.02, min(ms, 3.0))
    er = edge_rate if edge_rate is not None else os.getenv("YOUTOBE_DUB_EDGE_RATE", "-5%").strip() or "-5%"
    cps = chars_per_sec_target if chars_per_sec_target is not None else _float_env(
        "YOUTOBE_DUB_CPS_TARGET", 3.45
    )
    cps = max(2.2, min(cps, 5.5))
    if backend == "edge" and edge_tts is None:
        print("请先安装: pip install edge-tts", file=sys.stderr)
        sys.exit(1)
    asyncio.run(
        _build_async(
            video,
            zh_srt,
            out_audio,
            voice=voice,
            concurrency=concurrency,
            merge_repeats=merge_repeats,
            colloquial_openai=colloquial_openai,
            tts_polish_openai=tts_polish_openai,
            backend=backend,
            volc_format=volc_format,
            volc_sample_rate=volc_sample_rate,
            max_speedup=ms,
            edge_rate=er,
            edge_pitch=edge_pitch,
            sync_en_time=sync_en_time,
            duration_fit_openai=duration_fit_openai,
            chars_per_sec_target=cps,
            en_srt_explicit=en_srt,
        )
    )


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass

    def _resolve_backend(b: str) -> str:
        if b != "auto":
            return b
        if os.getenv("VOLCENGINE_TTS_API_KEY", "").strip():
            return "volc"
        if os.getenv("ELEVENLABS_API_KEY", "").strip():
            return "elevenlabs"
        return "edge"

    ap = argparse.ArgumentParser(
        description="中文配音轨（Edge / 火山 / ElevenLabs / auto）"
    )
    ap.add_argument("video", type=Path, help="参考时长的视频文件")
    ap.add_argument("zh_srt", type=Path, help="中文 SRT")
    ap.add_argument("out_audio", type=Path, help="输出 .wav 或 .m4a")
    ap.add_argument(
        "--backend",
        choices=("edge", "volc", "elevenlabs", "auto"),
        default="auto",
        help="auto：火山 Key > ElevenLabs Key > Edge；见 https://github.com/jin-wook-lee-96/ai-dubbing",
    )
    ap.add_argument(
        "--voice",
        default=EDGE_DEFAULT_VOICE,
        help="Edge 音色 / 火山 speaker / ElevenLabs voice_id（默认可用 ELEVENLABS_VOICE_ID）",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="并发数（Edge 5；火山 2–4；ElevenLabs 建议 2–3）",
    )
    ap.add_argument(
        "--volc-format",
        default="wav",
        choices=("wav", "mp3", "aac"),
        help="火山 TTS 音频格式（--backend volc 时有效）",
    )
    ap.add_argument(
        "--volc-sample-rate",
        type=int,
        default=24000,
        help="火山 TTS 采样率（Hz）",
    )
    ap.add_argument(
        "--merge-repeats",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="合并相邻重复/相似句（默认开，与 --no-merge-repeats 相对）",
    )
    ap.add_argument(
        "--colloquial-openai",
        action="store_true",
        help="OpenAI 口语化配音稿（需 OPENAI_API_KEY）",
    )
    ap.add_argument(
        "--tts-polish-openai",
        action="store_true",
        help="OpenAI 二次润色：适合 TTS 的标点与长度（需 OPENAI_API_KEY，建议在口语化之后）",
    )
    ap.add_argument(
        "--max-speedup",
        type=float,
        default=None,
        help="对齐时间轴时最大变速倍数（默认读环境变量 YOUTOBE_DUB_MAX_SPEEDUP 或 1.22）",
    )
    ap.add_argument(
        "--edge-rate",
        default=None,
        metavar="PCT",
        help="Edge 语速，如 -5%% 或 +0%%（默认读 YOUTOBE_DUB_EDGE_RATE 或 -5%%）",
    )
    ap.add_argument(
        "--edge-pitch",
        default="+0Hz",
        help="Edge 音高，如 +0Hz",
    )
    ap.add_argument(
        "--en-srt",
        type=Path,
        default=None,
        help="显式指定英文 SRT（默认同目录 foo.zh.srt → foo.en.srt）",
    )
    ap.add_argument(
        "--sync-en-time",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="与英文字幕时间轴对齐（默认开；需 .en.srt，与硬烧双语一致）",
    )
    ap.add_argument(
        "--duration-fit-openai",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="按英文句时长用 OpenAI 压缩中文口播（默认开，需 OPENAI_API_KEY 且为英文时间轴）",
    )
    ap.add_argument(
        "--dub-cps-target",
        type=float,
        default=None,
        help="口播密度目标（汉字/秒），默认读 YOUTOBE_DUB_CPS_TARGET 或 3.45",
    )
    args = ap.parse_args()

    backend = _resolve_backend(args.backend)
    voice = args.voice
    if backend == "elevenlabs":
        el_def = (
            os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL").strip()
            or "EXAVITQu4vr4xnSDxMaL"
        )
        if (
            voice == EDGE_DEFAULT_VOICE
            or voice.startswith("zh-CN-")
            or voice.startswith("zh_female")
        ):
            voice = el_def
    elif backend == "volc" and voice.startswith("zh-CN-"):
        voice = os.getenv("VOLC_TTS_VOICE", "zh_female_qingxin").strip() or "zh_female_qingxin"

    build_dub_track(
        args.video,
        args.zh_srt,
        args.out_audio,
        voice=voice,
        concurrency=args.concurrency,
        merge_repeats=args.merge_repeats,
        colloquial_openai=args.colloquial_openai,
        tts_polish_openai=args.tts_polish_openai,
        backend=backend,
        volc_format=args.volc_format,
        volc_sample_rate=args.volc_sample_rate,
        max_speedup=args.max_speedup,
        edge_rate=args.edge_rate,
        edge_pitch=args.edge_pitch,
        sync_en_time=args.sync_en_time,
        duration_fit_openai=args.duration_fit_openai,
        chars_per_sec_target=args.dub_cps_target,
        en_srt=args.en_srt,
    )
    print(str(args.out_audio.resolve()))


if __name__ == "__main__":
    main()
