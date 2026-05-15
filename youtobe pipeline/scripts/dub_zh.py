#!/usr/bin/env python3
"""
【模块】dub_zh.py — 中文配音轨：英文时间轴对齐、大模型口播/时长压缩、多后端 TTS、情绪 delivery 映射。
【调用方】命令行；run.py、finish_outputs.py 子进程调用。

根据中文 SRT 生成与画面时长对齐的中文配音音轨，导出 WAV/M4A。

- **--sync-en-time**（默认开）：若同目录存在 `{stem}.en.srt`，则配音起止时间与断句**与英文字幕一致**，
  与 `merge_bilingual_srt.py` 硬烧双语字幕对齐，解决「中文配音还在读、画面已切下一句」的错位。
- **--duration-fit-openai**（默认开，需已配置大模型：YOUTOBE_LLM_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY）：与 `subtitle_reading_time.py` 同一套 **CPS / over_ratio / under_ratio** 判定每条为 over/under；
  over 走大模型压缩口播，under 走与翻译阶段相同的 `zh_reading_time_align_batch` 略作充实，减轻吞字与句内过长留白。
- **句尾吞音**：在相邻字幕空隙内为每段 TTS 增加少量尾音时长（`YOUTOBE_DUB_TAIL_GRACE_MS`），
  并对硬截断处做极短淡出；思路接近生产级 TTS 管线中的「时间预算 + 尾音释放」，不依赖 Coqui 本体。
- --backend edge / volc / **elevenlabs** / **fish** / **auto**（auto：可选优先 Fish Speech HTTP，否则 火山 Key > ElevenLabs Key > Edge，见 env.example）
- 使用英文时间轴时**不再**做相邻句合并（避免破坏原片停顿）；纯中文时间轴时仍可用 **--merge-repeats**。
- **--max-speedup**：限制为对齐英文槽位时的最大加速倍数（默认约 1.22；超出则裁尾）。
  优先用 **ffmpeg atempo** 做时长压缩（比 pydub 更自然）；可用 `YOUTOBE_DUB_FFMPEG_ATEMPO=0` 关闭。
- Edge：**--edge-rate** 略放慢（默认 -5%%）+ 默认音色 **zh-CN-YunxiNeural**；合成优先 **WebSocket 流式收集音频**（常见 Edge TTS 实践），失败再回退 `save()`。
- **--colloquial-openai** / **--tts-polish-openai**：口播化与 TTS 标点润色（走 OpenAI 兼容 Chat，见 env.example）
- **--emotion-align**（默认开，需大模型 + 英文时间轴）：按英文表达强度推断逐条微调 Edge 语速/音高、ElevenLabs stability，或对 Fish Speech 中文稿加简短风格前缀（火山暂不支持逐条韵律 API）。
"""

from __future__ import annotations

import argparse
import asyncio
import io
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

from translation_clients import llm_configured


def _parse_edge_rate_pct(rate: str) -> float:
    m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)\s*%\s*$", (rate or "").strip())
    if not m:
        return -5.0
    return float(m.group(1))


def _format_edge_rate_pct(p: float) -> str:
    ip = int(round(max(-48.0, min(48.0, p))))
    if ip < 0:
        return f"{ip}%"
    return f"+{ip}%"


def _parse_edge_pitch_hz(pitch: str) -> float:
    m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)\s*Hz\s*$", (pitch or "").strip(), re.I)
    if not m:
        return 0.0
    return float(m.group(1))


def _format_edge_pitch_hz(hz: float) -> str:
    v = int(round(max(-18.0, min(18.0, hz))))
    if v >= 0:
        return f"+{v}Hz"
    return f"{v}Hz"


def _combine_edge_rate(base_rate: str, delta_pct: float) -> str:
    return _format_edge_rate_pct(_parse_edge_rate_pct(base_rate) + delta_pct)


def _combine_edge_pitch(base_pitch: str, delta_hz: float) -> str:
    return _format_edge_pitch_hz(_parse_edge_pitch_hz(base_pitch) + delta_hz)


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


_CLAUSE_SPLIT_RE = re.compile(r"(?<=[。！？；.!?])\s*")


def _split_clauses(text: str) -> list[str]:
    """按中英文句读切分，合并过短碎片。"""
    t = (text or "").replace("\n", " ").strip()
    if not t:
        return []
    raw = [p.strip() for p in _CLAUSE_SPLIT_RE.split(t) if p.strip()]
    if not raw:
        return [t]
    merged: list[str] = []
    min_chars = 6
    for p in raw:
        if merged and len(p) < min_chars:
            merged[-1] = (merged[-1] + p).strip()
        elif merged and len(merged[-1]) < min_chars:
            merged[-1] = (merged[-1] + p).strip()
        else:
            merged.append(p)
    return merged if merged else [t]


def _deoverlap_dub_segments(
    segments: list[tuple[int, int, str]],
    en_lines: list[str],
    *,
    gap_ms: int,
) -> tuple[list[tuple[int, int, str]], list[str]]:
    """消除重叠时间轴：后一条起点不早于前一条结束 + gap。"""
    if not segments:
        return segments, en_lines
    out_s: list[tuple[int, int, str]] = []
    out_e: list[str] = []
    for i, (sm, em, zh) in enumerate(segments):
        en = en_lines[i] if i < len(en_lines) else ""
        sm2 = int(sm)
        em2 = int(em)
        if out_s and sm2 < out_s[-1][1] + gap_ms:
            sm2 = out_s[-1][1] + gap_ms
        if em2 <= sm2:
            em2 = sm2 + max(280, em - sm)
        zh_s = (zh or "").strip()
        zh_keep = zh_s if zh_s else (zh or "")
        out_s.append((sm2, em2, zh_keep))
        out_e.append(en)
    return out_s, out_e


def _split_zh_char_balanced(zh: str, n: int) -> list[str]:
    """将中文均分为 n 段（按字符），用于长槽位内多段 TTS。"""
    t = (zh or "").replace("\n", " ").strip()
    if not t or n <= 1:
        return [t] if t else []
    L = len(t)
    n = min(n, L)
    base, rem = divmod(L, n)
    parts: list[str] = []
    i = 0
    for k in range(n):
        ln = base + (1 if k < rem else 0)
        chunk = t[i : i + ln].strip()
        i += ln
        if chunk:
            parts.append(chunk)
    return parts if parts else [t]


def _subdivide_cue_for_audio(
    sm: int,
    em: int,
    zh: str,
    *,
    max_slot_ms: int,
    min_piece_ms: int,
    next_start_ms: int,
    video_end_ms: int,
) -> list[tuple[int, int, str, int]]:
    """
    单条字幕槽位内切分为多段口播，不增加字幕条数，仅用于 TTS 时间分配。
    返回 (sub_sm, sub_em, sub_zh, playback_ms)；playback 含末段可选句尾留白。
    """
    zh_s = (zh or "").replace("\n", " ").strip()
    dur = int(em) - int(sm)
    if dur < 250 or not zh_s:
        return []
    gap = _int_env("YOUTOBE_DUB_OVERLAP_GAP_MS", 48, lo=16, hi=240)
    grace = _int_env("YOUTOBE_DUB_TAIL_GRACE_MS", 90, lo=0, hi=260)
    safe = _int_env("YOUTOBE_DUB_TAIL_GAP_SAFE_MS", 45, lo=12, hi=200)
    eff_end = min(int(em), int(next_start_ms) - gap, int(video_end_ms))
    if eff_end <= sm + min_piece_ms:
        eff_end = sm + max(min_piece_ms, dur)
    dur_vis = eff_end - sm
    if dur_vis < min_piece_ms:
        return [(sm, eff_end, zh_s, eff_end - sm)]

    n_parts = max(1, int((dur_vis + max_slot_ms - 1) // max_slot_ms))
    if n_parts <= 1:
        play = dur_vis + min(grace, max(0, next_start_ms - safe - eff_end))
        play = min(play, max(dur_vis, video_end_ms - sm - 40))
        return [(sm, eff_end, zh_s, max(dur_vis, play))]

    parts = _split_zh_char_balanced(zh_s, n_parts)
    weights = [max(1, len(p)) for p in parts]
    tw = sum(weights)
    out: list[tuple[int, int, str, int]] = []
    cursor = int(sm)
    for j, p in enumerate(parts):
        if not p.strip():
            continue
        if j == len(parts) - 1:
            sub_sm = cursor
            sub_em = int(eff_end)
        else:
            share = weights[j] / tw
            sub_dur = max(min_piece_ms, int(dur_vis * share))
            sub_sm = cursor
            sub_em = min(cursor + sub_dur, eff_end - min_piece_ms * (len(parts) - j - 1))
            if sub_em <= sub_sm:
                sub_em = sub_sm + min_piece_ms
            cursor = sub_em
        fit_ms = sub_em - sub_sm
        if fit_ms < 250:
            continue
        play_ms = fit_ms
        if j == len(parts) - 1:
            extra = min(grace, max(0, next_start_ms - safe - sub_em))
            extra = min(extra, max(0, video_end_ms - sub_em - 80))
            play_ms = fit_ms + max(0, extra)
        out.append((sub_sm, sub_em, p.strip(), play_ms))
    return out


def _normalize_dub_timeline(
    segments: list[tuple[int, int, str]],
    en_lines: list[str] | None,
) -> tuple[list[tuple[int, int, str]], list[str] | None]:
    """口语化/时长润色之后：仅去重叠，不改变条数（双语 merge 与 en 索引一致）。"""
    if not segments:
        return segments, en_lines
    el = en_lines if en_lines is not None else [""] * len(segments)
    if os.getenv("YOUTOBE_DUB_FIX_OVERLAP", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    ):
        gap = _int_env("YOUTOBE_DUB_OVERLAP_GAP_MS", 48, lo=16, hi=240)
        segments, el2 = _deoverlap_dub_segments(segments, el, gap_ms=gap)
        if en_lines is not None:
            en_lines = el2
    return segments, en_lines


def _last_end_ms_from_subs(subs: list) -> int:
    if not subs:
        return 0
    return _time_to_ms(subs[-1].end)


def _reading_time_align_segments_for_dub(
    segments: list[tuple[int, int, str]],
    en_lines: list[str],
    *,
    chars_per_sec_target: float | None,
    skip_llm: bool = False,
) -> list[tuple[int, int, str]]:
    """
    与 translate_srt 一致的 subtitle_reading_time 分级：over → 压缩口播，under → 充实口播。
    CPS 默认 reading_align_thresholds()；若传入 chars_per_sec_target 则仅覆盖 CPS，over/under 阈值仍读环境变量。
    skip_llm=True 时只做扫描与日志，不改写（用于未配置大模型 Key 时的排障提示）。
    """
    from subtitle_reading_time import classify_reading_alignment, reading_align_thresholds

    from translation_clients import openai_dub_duration_fit_batch, zh_reading_time_align_batch

    if len(segments) != len(en_lines):
        raise RuntimeError("英文句与中文段数量不一致")

    cps, over_r, under_r, min_u_slot, u_bias = reading_align_thresholds()
    if chars_per_sec_target is not None:
        cps = max(2.2, min(float(chars_per_sec_target), 5.5))

    over_idx: list[int] = []
    under_idx: list[int] = []
    sk = ok_c = ov = un = 0
    for i, (sm, em, zh) in enumerate(segments):
        zh_s = (zh or "").strip()
        slot_sec = max(0.04, (em - sm) / 1000.0)
        if slot_sec < 0.22 or not zh_s:
            sk += 1
            continue
        issue = classify_reading_alignment(
            zh_s,
            slot_sec,
            cps=cps,
            over_ratio=over_r,
            under_ratio=under_r,
            min_slot_for_under=min_u_slot,
            units_tts_bias=u_bias,
        )
        if issue == "over":
            ov += 1
            over_idx.append(i)
        elif issue == "under":
            un += 1
            under_idx.append(i)
        elif issue == "ok":
            ok_c += 1
        else:
            sk += 1

    n = len(segments)
    print(
        f"配音朗读对齐(subtitle_reading_time): 扫描 {n} 条（skip {sk} / ok {ok_c} / over {ov} / under {un}），"
        f"CPS≈{cps:.2f}；待改写 over {len(over_idx)} / under {len(under_idx)}。",
        file=sys.stderr,
    )

    out_text = [s[2] for s in segments]
    if not over_idx and not under_idx:
        return [(segments[i][0], segments[i][1], out_text[i]) for i in range(len(segments))]

    if skip_llm:
        print(
            "配音朗读对齐: 未配置大模型 Key，跳过 over/under 自动改写（仍依赖 TTS 后变速/静音填充）。",
            file=sys.stderr,
        )
        return [(segments[i][0], segments[i][1], out_text[i]) for i in range(len(segments))]

    chunk_over = int(os.getenv("YOUTOBE_DUB_READING_OVER_CHUNK", "10").strip() or "10")
    chunk_over = max(4, min(chunk_over, 14))
    chunk_u = (
        os.getenv("YOUTOBE_DUB_READING_UNDER_CHUNK", "").strip()
        or os.getenv("YOUTOBE_TRANSLATE_READING_ALIGN_CHUNK", "12").strip()
        or "12"
    )
    chunk_under = max(4, min(int(chunk_u), 18))

    n_api = 0

    def _slot_sec(i: int) -> float:
        sm, em, _ = segments[i]
        return max(0.06, (em - sm) / 1000.0)

    # 先压缩 over，再充实 under（避免先拉长后又超长）
    for a in range(0, len(over_idx), chunk_over):
        batch_i = over_idx[a : a + chunk_over]
        buf = [
            (en_lines[i], out_text[i], _slot_sec(i))
            for i in batch_i
        ]
        try:
            got = openai_dub_duration_fit_batch(
                buf, api_key=None, chars_per_sec_budget=cps
            )
        except Exception as e:
            print(f"配音 over 压缩: 本批 API 失败，跳过: {e}", file=sys.stderr)
            continue
        if len(got) != len(batch_i):
            print("配音 over 压缩: 返回条数不一致，跳过该批。", file=sys.stderr)
            continue
        for k, i in enumerate(batch_i):
            out_text[i] = got[k]
        n_api += len(batch_i)
        time.sleep(0.18)

    for a in range(0, len(under_idx), chunk_under):
        batch_i = under_idx[a : a + chunk_under]
        items: list[tuple[str, str, float, str]] = []
        for i in batch_i:
            en = (en_lines[i] if i < len(en_lines) else "") or ""
            items.append((en, out_text[i], _slot_sec(i), "under"))
        try:
            got = zh_reading_time_align_batch(
                items, api_key=None, chars_per_sec_budget=cps
            )
        except Exception as e:
            print(f"配音 under 充实: 本批 API 失败，跳过: {e}", file=sys.stderr)
            continue
        if len(got) != len(batch_i):
            print("配音 under 充实: 返回条数不一致，跳过该批。", file=sys.stderr)
            continue
        for k, i in enumerate(batch_i):
            out_text[i] = got[k]
        n_api += len(batch_i)
        time.sleep(0.18)

    if n_api:
        print(
            f"配音朗读对齐(大模型): 已改写 {n_api} 条（含 over 压缩与 under 充实）。",
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
) -> list[tuple[int, int, str]]:
    from translation_clients import openai_colloquial_zh_batch

    texts = [s[2] for s in segments]
    out: list[str] = []
    chunk = 14
    for i in range(0, len(texts), chunk):
        batch = texts[i : i + chunk]
        got = openai_colloquial_zh_batch(batch, api_key=None)
        if len(got) != len(batch):
            raise RuntimeError("口语化 API 返回条数与请求不一致")
        out.extend(got)
        time.sleep(0.22)
    return [(segments[j][0], segments[j][1], out[j]) for j in range(len(segments))]


def _tts_polish_segments(
    segments: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    from translation_clients import openai_tts_polish_batch

    texts = [s[2] for s in segments]
    out: list[str] = []
    chunk = int(os.getenv("YOUTOBE_DUB_TTS_POLISH_CHUNK", "8").strip() or "8")
    chunk = max(3, min(chunk, 12))

    def _polish_one(t: str) -> str:
        try:
            one = openai_tts_polish_batch([t], api_key=None)
            if len(one) == 1:
                return str(one[0]).strip()
        except Exception:
            pass
        return t

    for i in range(0, len(texts), chunk):
        batch = texts[i : i + chunk]
        got: list[str] | None = None
        try:
            cand = openai_tts_polish_batch(batch, api_key=None)
            if len(cand) == len(batch):
                got = [str(x).strip() for x in cand]
        except Exception as e:
            print(f"TTS 润色: 批量 {i + 1}-{i + len(batch)} 失败 ({e})，改为逐条…", file=sys.stderr)
        if got is None:
            got = []
            for t in batch:
                got.append(_polish_one(t))
                time.sleep(0.12)
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


def _int_env(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        v = int(float(os.getenv(name, str(default)).strip()))
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def _fade_truncated_tail(seg: AudioSegment) -> AudioSegment:
    """硬截断前极短淡出，减轻尾字「咔嚓」感（与神经网络 TTS 常见尾音处理思路一致）。"""
    if len(seg) < 16:
        return seg
    fd = min(36, max(8, len(seg) // 12))
    return seg.fade_out(fd)


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
        return _fade_truncated_tail(seg[:slot_ms])
    use_ratio = min(ratio, max(1.01, max_speedup))
    if use_ratio <= trunc_max:
        return _fade_truncated_tail(seg[:slot_ms])
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
            return _fade_truncated_tail(seg[:slot_ms])
        use_ratio = min(ratio, max(1.01, max_speedup))
        try:
            seg = seg.speedup(playback_speed=use_ratio)
        except Exception:
            seg = seg._spawn(
                seg.raw_data,
                overrides={"frame_rate": int(seg.frame_rate * use_ratio)},
            ).set_frame_rate(seg.frame_rate)
    if len(seg) > slot_ms:
        seg = _fade_truncated_tail(seg[:slot_ms])
    if len(seg) < slot_ms:
        seg = seg + AudioSegment.silent(duration=slot_ms - len(seg))
    return seg


async def _edge_synthesize_mp3_bytes(
    text: str,
    voice: str,
    *,
    rate: str,
    pitch: str,
    proxy: str | None,
) -> bytes:
    """
    按 WebSocket 流聚合 MP3（与常见 Edge TTS / edge-tts 实践一致），
    见: https://cloud.tencent.com/developer/article/2641972
    流式失败或数据过短时回退一次 Communicate.save 到临时文件再读回。
    """
    if not (text or "").strip():
        return b""

    def _communicate():
        if proxy:
            return edge_tts.Communicate(
                text, voice, rate=rate, pitch=pitch, proxy=proxy
            )
        return edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)

    com = _communicate()
    buf = io.BytesIO()
    try:
        async for chunk in com.stream():
            if not isinstance(chunk, dict):
                continue
            data = chunk.get("data")
            if isinstance(data, (bytes, bytearray)) and len(data) > 8:
                buf.write(data)
    except Exception:
        pass
    raw = buf.getvalue()
    if len(raw) >= 64:
        return raw

    com2 = _communicate()
    fd, name = tempfile.mkstemp(suffix="_edgefb.mp3")
    os.close(fd)
    tmp = Path(name)
    try:
        await com2.save(str(tmp))
        b = tmp.read_bytes()
        if len(b) >= 64:
            return b
    finally:
        tmp.unlink(missing_ok=True)
    return raw


def _write_silent_edge_placeholder(path: Path, duration_ms: int) -> None:
    """写入可被 pydub/ffprobe 识别的静音（优先 ffmpeg 生成标准 MP3，否则写 WAV 字节供 _load_clip 回退解析）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    sec = max(0.55, min(8.0, duration_ms / 1000.0))
    ff = shutil.which("ffmpeg")
    if ff:
        r = subprocess.run(
            [
                ff,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                f"{sec:.3f}",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "64k",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and path.exists() and path.stat().st_size > 64:
            return
        if r.stderr:
            print(f"提示: ffmpeg 静音占位: {r.stderr[:200]}", file=sys.stderr)
    seg = AudioSegment.silent(duration=int(sec * 1000))
    bio = io.BytesIO()
    seg.export(bio, format="wav")
    path.write_bytes(bio.getvalue())


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

    def _write_silent_placeholder() -> None:
        tlen = len((text or "").strip())
        ms = max(500, min(2800, 80 + tlen * 42))
        _write_silent_edge_placeholder(path, ms)

    net_rounds = _int_env("YOUTOBE_EDGE_TTS_NET_ROUNDS", 10, lo=2, hi=24)
    inner_try = _int_env("YOUTOBE_EDGE_TTS_INNER_TRIES", 6, lo=2, hi=14)
    last_err: Exception | None = None
    for net in range(net_rounds):
        for q in range(inner_try):
            try:
                raw = await _edge_synthesize_mp3_bytes(
                    text, voice, rate=rate, pitch=pitch, proxy=proxy
                )
                if len(raw) < 64:
                    raise RuntimeError("Edge TTS 合成数据过短")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                seg = AudioSegment.from_file(str(path), format="mp3")
                if not _tts_raw_clip_bad(seg, text):
                    return
                print(
                    f"Edge TTS 输出过短或过静 (len={len(seg)}ms, rms≈{seg.rms})，"
                    f"质量重试 {q + 1}/{inner_try}…",
                    file=sys.stderr,
                )
                last_err = RuntimeError("Edge TTS 过短/过静")
                await asyncio.sleep(0.35 + 0.22 * q)
            except Exception as e:
                last_err = e
                print(f"Edge TTS 请求异常: {e!s}", file=sys.stderr)
                em = str(e).lower()
                if "no audio" in em or type(e).__name__ == "NoAudioReceived":
                    await asyncio.sleep(0.55 + 0.65 * q + 0.14 * net)
                else:
                    await asyncio.sleep(0.22 + 0.18 * q)
        if net < net_rounds - 1:
            wait = min(24.0, 1.15 * (2 ** min(net, 7)))
            print(
                f"Edge TTS 网络冷却 ({net + 1}/{net_rounds}) 等待 {wait:.1f}s…",
                file=sys.stderr,
            )
            await asyncio.sleep(wait)
    if os.getenv("YOUTOBE_EDGE_TTS_FAIL_SILENT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    ):
        print(
            "警告: Edge TTS 本条在多次重试后仍失败，已写入短静音占位以便成片继续；"
            "请配置 YOUTOBE_EDGE_TTS_PROXY / HTTPS_PROXY 或改用 --backend volc 后重跑 finalize。",
            file=sys.stderr,
        )
        _write_silent_placeholder()
        return
    assert last_err is not None
    raise last_err


def _tts_volc_save_validated(
    text: str,
    path: Path,
    voice: str,
    response_format: str,
    sample_rate: int,
) -> None:
    if not os.getenv("VOLCENGINE_TTS_API_KEY", "").strip():
        raise RuntimeError("缺少 VOLCENGINE_TTS_API_KEY，无法调用火山 TTS")
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
                em = str(e)
                if "缺少 VOLCENGINE_TTS_API_KEY" in em:
                    raise
                break
        else:
            last_err = last_err or RuntimeError("火山 TTS 多次合成仍为过短/过静")
        if net < 4:
            time.sleep(1.2 * (2**min(net, 3)))
    assert last_err is not None
    raise last_err


def _tts_fish_save_validated(
    text: str,
    path: Path,
    dstyle: dict | None,
    *,
    reference_id: str | None,
) -> None:
    from fish_speech_tts_client import synthesize_fish_speech_wav

    last_err: Exception | None = None
    for net in range(4):
        for q in range(4):
            try:
                path.unlink(missing_ok=True)
                synthesize_fish_speech_wav(
                    text,
                    path,
                    dstyle,
                    reference_id=reference_id,
                )
                seg = AudioSegment.from_file(str(path), format="wav")
                if not _tts_raw_clip_bad(seg, text):
                    return
                print(
                    f"Fish Speech 输出过短或过静 (len={len(seg)}ms)，质量重试 {q + 1}/4…",
                    file=sys.stderr,
                )
                time.sleep(0.45 + 0.2 * q)
            except Exception as e:
                last_err = e
                print(f"Fish Speech 异常: {e!s}", file=sys.stderr)
                break
        else:
            last_err = last_err or RuntimeError("Fish Speech 多次合成仍为过短/过静")
        if net < 3:
            time.sleep(1.0 * (2**min(net, 3)))
    assert last_err is not None
    raise last_err


def _fish_reference_id(cli_voice: str) -> str | None:
    envr = os.getenv("YOUTOBE_FISH_SPEECH_REFERENCE_ID", "").strip()
    if envr:
        return envr
    v = (cli_voice or "").strip()
    if not v:
        return None
    if v.startswith("zh-CN-") or v.startswith("zh_female") or v.startswith("EXAVIT"):
        return None
    return v


def _tts_elevenlabs_save_validated(
    text: str,
    path: Path,
    voice_id: str,
    *,
    stability: float | None = None,
    similarity_boost: float | None = None,
) -> None:
    last_err: Exception | None = None
    for net in range(5):
        for q in range(6):
            try:
                path.unlink(missing_ok=True)
                _tts_elevenlabs_save(
                    text,
                    path,
                    voice_id,
                    stability=stability,
                    similarity_boost=similarity_boost,
                )
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


def _tts_elevenlabs_save(
    text: str,
    path: Path,
    voice_id: str,
    *,
    stability: float | None = None,
    similarity_boost: float | None = None,
) -> None:
    from elevenlabs_tts_client import synthesize_elevenlabs_tts

    data = synthesize_elevenlabs_tts(
        text,
        voice_id=voice_id,
        stability=stability,
        similarity_boost=similarity_boost,
    )
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
        for fmt in ("mp3", "wav"):
            try:
                if not path.exists() or path.stat().st_size < 32:
                    continue
                return AudioSegment.from_file(str(path), format=fmt)
            except Exception:
                continue
        raise RuntimeError(f"无法解析 TTS 文件（mp3/wav 均失败）: {path}")
    if backend in ("fish",):
        return AudioSegment.from_file(str(path), format="wav")
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
    emotion_align: bool,
) -> None:
    _ensure_ffmpeg_tools()
    fish_ref: str | None = None
    if backend == "fish":
        fish_ref = _fish_reference_id(voice)
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
        if not llm_configured():
            print(
                "警告: --colloquial-openai 需要 YOUTOBE_LLM_API_KEY、DEEPSEEK_API_KEY 或 OPENAI_API_KEY，已跳过口语化。",
                file=sys.stderr,
            )
        else:
            print("口语化: 使用大模型优化配音稿…", file=sys.stderr)
            segments = _colloquialize_segments(segments)

    if duration_fit_openai and en_lines is not None:
        if not llm_configured():
            print(
                "警告: --duration-fit-openai 需要 YOUTOBE_LLM_API_KEY、DEEPSEEK_API_KEY 或 OPENAI_API_KEY，已跳过口播改写。",
                file=sys.stderr,
            )
            segments = _reading_time_align_segments_for_dub(
                segments,
                en_lines,
                chars_per_sec_target=chars_per_sec_target,
                skip_llm=True,
            )
        else:
            print(
                "朗读对齐: 按 subtitle_reading_time 与翻译阶段相同阈值扫描 over/under，"
                "大模型压缩过长口播并充实过短口播…",
                file=sys.stderr,
            )
            segments = _reading_time_align_segments_for_dub(
                segments,
                en_lines,
                chars_per_sec_target=chars_per_sec_target,
                skip_llm=False,
            )

    if tts_polish_openai:
        if not llm_configured():
            print(
                "警告: --tts-polish-openai 需要 YOUTOBE_LLM_API_KEY、DEEPSEEK_API_KEY 或 OPENAI_API_KEY，已跳过 TTS 润色。",
                file=sys.stderr,
            )
        else:
            print("TTS 润色: 大模型优化断句与符号…", file=sys.stderr)
            segments = _tts_polish_segments(segments)

    segments, en_lines = _normalize_dub_timeline(segments, en_lines)

    style_by_seg: list[dict[str, float] | None] = [None] * len(segments)
    if emotion_align and llm_configured() and en_lines is not None:
        if len(en_lines) != len(segments):
            print(
                "提示: 配音情绪对齐需英文字幕与中文配音段条数一致，已跳过韵律推断。",
                file=sys.stderr,
            )
        else:
            try:
                from translation_clients import dub_delivery_style_batch

                ch = int(os.getenv("YOUTOBE_DUB_EMOTION_CHUNK", "10").strip() or "10")
                ch = max(4, min(ch, 16))
                print(
                    "配音情绪: 按英文表达强度推断逐条韵律（大模型，映射 Edge / ElevenLabs / Fish Speech）…",
                    file=sys.stderr,
                )
                for a in range(0, len(segments), ch):
                    b = min(len(segments), a + ch)
                    pairs = [(en_lines[i], segments[i][2]) for i in range(a, b)]
                    rows_m = dub_delivery_style_batch(pairs, api_key=None)
                    for k, i in enumerate(range(a, b)):
                        if k < len(rows_m):
                            style_by_seg[i] = rows_m[k]
                    time.sleep(0.22)
                print("配音情绪: 推断完成。", file=sys.stderr)
            except Exception as e:
                print(f"配音情绪: 推断失败，已用默认平淡参数: {e}", file=sys.stderr)
    elif emotion_align and en_lines is None:
        print(
            "提示: 无英文字幕时间轴参考，跳过与英文朗读者情绪对齐。",
            file=sys.stderr,
        )

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
            cap_e = int(os.getenv("YOUTOBE_EDGE_TTS_UNPROXIED_CONCURRENCY", "2").strip() or "2")
            cap_e = max(1, min(cap_e, 8))
            if concurrency > cap_e:
                print(
                    f"提示: Edge 未走代理时并发过高易触发 NoAudioReceived，"
                    f"已将配音并发 {concurrency} → {cap_e}（可用 YOUTOBE_EDGE_TTS_UNPROXIED_CONCURRENCY 覆盖）。",
                    file=sys.stderr,
                )
                concurrency = cap_e
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
    elif backend == "fish":
        from fish_speech_tts_client import fish_speech_base_url

        print(
            f"配音后端: Fish Speech HTTP（{fish_speech_base_url()}，需已启动官方 api_server）",
            file=sys.stderr,
        )
        if fish_ref:
            print(f"Fish 参考音色 reference_id: {fish_ref}", file=sys.stderr)
        else:
            print(
                "提示: 未设置 YOUTOBE_FISH_SPEECH_REFERENCE_ID 且 --voice 为 Edge 默认；"
                "请配置参考音或在服务器 references 目录放置音色。",
                file=sys.stderr,
            )
    elif backend == "volc":
        print(
            f"配音后端: 火山 OpenSpeech TTS（{volc_format} {volc_sample_rate}Hz）",
            file=sys.stderr,
        )
        if emotion_align and any(style_by_seg):
            print(
                "提示: 火山 OpenSpeech 当前封装未接逐条情感 API；情绪对齐主要体现在中文稿与句间停顿。",
                file=sys.stderr,
            )

    tmpdir = Path(tempfile.mkdtemp(prefix="ytdub_"))
    sem = asyncio.Semaphore(max(1, concurrency))
    ext = (
        "mp3"
        if backend in ("edge", "elevenlabs")
        else ("wav" if backend in ("fish",) else volc_format)
    )

    max_slot_ms = _int_env("YOUTOBE_DUB_MAX_SLOT_MS", 11000, lo=4000, hi=90000)
    min_piece_ms = _int_env("YOUTOBE_DUB_MIN_SUBPIECE_MS", 380, lo=200, hi=2000)

    jobs: list[
        tuple[int, int, int, Path, str, str, str, float, float, dict | None]
    ] = []
    for i, (start_ms, end_ms, text) in enumerate(segments):
        raw_full = _sanitize_tts_text(text)
        if not raw_full.strip():
            continue
        next_start = int(segments[i + 1][0]) if i + 1 < len(segments) else dur_ms
        subs = _subdivide_cue_for_audio(
            int(start_ms),
            int(end_ms),
            raw_full,
            max_slot_ms=max_slot_ms,
            min_piece_ms=min_piece_ms,
            next_start_ms=next_start,
            video_end_ms=dur_ms,
        )
        if not subs:
            continue
        dstyle = style_by_seg[i] if i < len(style_by_seg) else None
        dr = dp = 0.0
        st11, sm11 = 0.5, 0.75
        if dstyle is not None:
            dr = float(dstyle.get("rate_delta_pct", 0) or 0)
            dp = float(dstyle.get("pitch_delta_hz", 0) or 0)
            st11 = float(dstyle.get("eleven_stability", 0.5) or 0.5)
            sm11 = float(dstyle.get("eleven_similarity_boost", 0.75) or 0.75)
        if backend == "edge":
            rate_i = _combine_edge_rate(edge_rate, dr)
            pitch_i = _combine_edge_pitch(edge_pitch, dp)
        else:
            rate_i, pitch_i = edge_rate, edge_pitch
        for j, (sub_sm, sub_em, sub_zh, play_ms) in enumerate(subs):
            raw = _sanitize_tts_text(sub_zh)
            if not raw.strip():
                continue
            fit_ms = max(250, int(sub_em) - int(sub_sm))
            pad_ms = max(fit_ms, int(play_ms))
            clip = tmpdir / f"c_{i}_{j}.{ext}"
            jobs.append(
                (sub_sm, fit_ms, pad_ms, clip, raw, rate_i, pitch_i, st11, sm11, dstyle)
            )

    async def _one_edge(
        clip: Path,
        text: str,
        rate: str,
        pitch: str,
    ) -> None:
        async with sem:
            await _tts_edge_save(
                text,
                clip,
                voice,
                rate=rate,
                pitch=pitch,
                proxy=edge_proxy,
            )

    async def _one_volc(clip: Path, text: str) -> None:
        async with sem:
            await asyncio.to_thread(
                _tts_volc_save_validated,
                text,
                clip,
                voice,
                volc_format,
                volc_sample_rate,
            )

    async def _one_eleven(
        clip: Path,
        text: str,
        stability: float,
        similarity_boost: float,
    ) -> None:
        async with sem:
            await asyncio.to_thread(
                _tts_elevenlabs_save_validated,
                text,
                clip,
                voice,
                stability=stability,
                similarity_boost=similarity_boost,
            )

    async def _one_fish(
        clip: Path,
        text: str,
        dstyle_seg: dict | None,
    ) -> None:
        async with sem:
            await asyncio.to_thread(
                _tts_fish_save_validated,
                text,
                clip,
                dstyle_seg,
                reference_id=fish_ref,
            )

    if backend == "edge":
        await asyncio.gather(
            *[
                _one_edge(p, tx, er, pi)
                for (_sm, _fi, _pm, p, tx, er, pi, _st, _si, _ds) in jobs
            ]
        )
    elif backend == "elevenlabs":
        await asyncio.gather(
            *[
                _one_eleven(p, tx, st, sim)
                for (_sm, _fi, _pm, p, tx, _er, _pi, st, sim, _ds) in jobs
            ]
        )
    elif backend == "fish":
        await asyncio.gather(
            *[
                _one_fish(p, tx, ds)
                for (_sm, _fi, _pm, p, tx, _er, _pi, _st, _si, ds) in jobs
            ]
        )
    else:
        await asyncio.gather(
            *[_one_volc(p, tx) for (_sm, _fi, _pm, p, tx, *_r) in jobs]
        )

    ordered = sorted(jobs, key=lambda x: (x[0], str(x[3])))
    for overlay_sm, fit_ms, pad_ms, clip, *_r in ordered:
        if not clip.exists():
            continue
        seg = _load_clip(clip, backend, volc_format)
        seg = _fit_segment(seg, fit_ms, max_speedup=max_speedup, workdir=tmpdir)
        if len(seg) < pad_ms:
            seg = seg + AudioSegment.silent(duration=pad_ms - len(seg))
        elif len(seg) > pad_ms:
            seg = _fade_truncated_tail(seg[:pad_ms])
        base = base.overlay(seg, position=overlay_sm)

    shutil.rmtree(tmpdir, ignore_errors=True)

    if len(base) < dur_ms:
        base = base + AudioSegment.silent(duration=dur_ms - len(base))

    out_audio.parent.mkdir(parents=True, exist_ok=True)
    suf = out_audio.suffix.lower()
    if suf in (".m4a", ".aac", ".mp4"):
        base.export(str(out_audio), format="ipod", bitrate="256k")
    else:
        base.export(str(out_audio), format="wav")


def _coerce_tts_backend_voice(backend: str, voice: str) -> tuple[str, str]:
    """
    显式选某后端但缺 Key / 服务不可用时降级，避免对每条字幕反复重试刷日志、拖死进程。
    Fish Speech HTTP 不可用：火山（有 Key）→ ElevenLabs（有 Key）→ Edge。
    volc 缺 Key：ElevenLabs（若有 Key）→ Edge。
    """
    has_volc = bool(os.getenv("VOLCENGINE_TTS_API_KEY", "").strip())
    has_11 = bool(os.getenv("ELEVENLABS_API_KEY", "").strip())
    volc_def = os.getenv("VOLC_TTS_VOICE", "zh_female_qingxin").strip() or "zh_female_qingxin"
    el_def = (
        os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL").strip()
        or "EXAVITQu4vr4xnSDxMaL"
    )

    if backend == "fish":
        fish_ok = False
        try:
            from fish_speech_tts_client import fish_speech_available

            fish_ok = fish_speech_available()
        except ImportError:
            fish_ok = False
        if not fish_ok:
            print(
                "警告: Fish Speech HTTP 不可用（未启动 api_server 或 YOUTOBE_FISH_SPEECH_URL 不可达）；"
                "已自动降级以免在每条字幕上卡住。",
                file=sys.stderr,
            )
            if has_volc:
                nv = voice.strip() if voice.strip().startswith("zh_female") else volc_def
                print("提示: 已改用火山 TTS。", file=sys.stderr)
                return "volc", nv
            if has_11:
                print("提示: 已改用 ElevenLabs TTS。", file=sys.stderr)
                return "elevenlabs", el_def
            print("提示: 已改用 Edge TTS（国内常需 YOUTOBE_EDGE_TTS_PROXY）。", file=sys.stderr)
            if voice.startswith("zh-CN-"):
                return "edge", voice
            return "edge", EDGE_DEFAULT_VOICE

    if backend == "volc" and not has_volc:
        print(
            "警告: 未配置 VOLCENGINE_TTS_API_KEY，无法使用火山 TTS；"
            "已自动降级以免在每条字幕上重复报错。",
            file=sys.stderr,
        )
        if has_11:
            print("提示: 已改用 ElevenLabs TTS。", file=sys.stderr)
            return "elevenlabs", el_def
        print("提示: 已改用 Edge TTS（国内常需 YOUTOBE_EDGE_TTS_PROXY）。", file=sys.stderr)
        if voice.startswith("zh-CN-"):
            return "edge", voice
        return "edge", EDGE_DEFAULT_VOICE
    if backend == "elevenlabs" and not has_11:
        print(
            "警告: 未配置 ELEVENLABS_API_KEY，已改用 Edge TTS。",
            file=sys.stderr,
        )
        if voice.startswith("zh-CN-"):
            return "edge", voice
        return "edge", EDGE_DEFAULT_VOICE
    return backend, voice


def build_dub_track(
    video: Path,
    zh_srt: Path,
    out_audio: Path,
    *,
    voice: str = EDGE_DEFAULT_VOICE,
    concurrency: int = 5,
    merge_repeats: bool = True,
    colloquial_openai: bool = True,
    tts_polish_openai: bool = True,
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
    emotion_align: bool | None = None,
) -> None:
    backend, voice = _coerce_tts_backend_voice(backend, voice)
    em = emotion_align
    if em is None:
        em = os.getenv("YOUTOBE_DUB_EMOTION_ALIGN", "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
            "",
        )
    ms = max_speedup if max_speedup is not None else _float_env("YOUTOBE_DUB_MAX_SPEEDUP", 1.22)
    ms = max(1.02, min(ms, 3.0))
    er = edge_rate if edge_rate is not None else os.getenv("YOUTOBE_DUB_EDGE_RATE", "-5%").strip() or "-5%"
    cps = chars_per_sec_target if chars_per_sec_target is not None else _float_env(
        "YOUTOBE_DUB_CPS_TARGET", 3.45
    )
    cps = max(2.2, min(cps, 5.5))
    conc = concurrency
    if backend == "fish":
        cap = int(os.getenv("YOUTOBE_FISH_SPEECH_MAX_CONCURRENCY", "2").strip() or "2")
        cap = max(1, min(cap, 4))
        if concurrency > cap:
            print(
                f"提示: Fish Speech 服务端建议低并发，已将并发从 {concurrency} 限制为 {cap}（可调 YOUTOBE_FISH_SPEECH_MAX_CONCURRENCY）。",
                file=sys.stderr,
            )
        conc = min(max(1, concurrency), cap)
    if backend == "edge" and edge_tts is None:
        print("请先安装: pip install edge-tts", file=sys.stderr)
        sys.exit(1)
    asyncio.run(
        _build_async(
            video,
            zh_srt,
            out_audio,
            voice=voice,
            concurrency=conc,
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
            emotion_align=em,
        )
    )


def main() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None  # type: ignore[misc, assignment]
    if load_dotenv is not None:
        _root = Path(__file__).resolve().parent.parent
        fd = _root / "config" / "feature_defaults.env"
        if fd.is_file():
            load_dotenv(fd, override=False)
        load_dotenv(_root / ".env", override=True)

    def _resolve_backend(b: str) -> str:
        if b != "auto":
            return b
        if os.getenv("YOUTOBE_DUB_PREFER_FISH_SPEECH", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            try:
                from fish_speech_tts_client import fish_speech_available

                if fish_speech_available():
                    return "fish"
            except ImportError:
                pass
        if os.getenv("VOLCENGINE_TTS_API_KEY", "").strip():
            return "volc"
        if os.getenv("ELEVENLABS_API_KEY", "").strip():
            return "elevenlabs"
        return "edge"

    ap = argparse.ArgumentParser(
        description="中文配音轨（Edge / 火山 / ElevenLabs / Fish Speech / auto）"
    )
    ap.add_argument("video", type=Path, help="参考时长的视频文件")
    ap.add_argument("zh_srt", type=Path, help="中文 SRT")
    ap.add_argument("out_audio", type=Path, help="输出 .wav 或 .m4a")
    ap.add_argument(
        "--backend",
        choices=("edge", "volc", "elevenlabs", "fish", "auto"),
        default="auto",
        help=(
            "auto：若 YOUTOBE_DUB_PREFER_FISH_SPEECH=1 且 Fish Speech HTTP 可用则优先 fish；"
            "否则 火山 Key > ElevenLabs Key > Edge"
        ),
    )
    ap.add_argument(
        "--voice",
        default=EDGE_DEFAULT_VOICE,
        help=(
            "Edge 音色 / 火山 speaker / ElevenLabs voice_id；"
            "Fish Speech：若非 Edge 默认字符串则作为 reference_id（可被 YOUTOBE_FISH_SPEECH_REFERENCE_ID 覆盖）"
        ),
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="并发数（Edge 5；火山 2–4；ElevenLabs 2–3；Fish Speech 见 YOUTOBE_FISH_SPEECH_MAX_CONCURRENCY）",
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
        action=argparse.BooleanOptionalAction,
        default=True,
        help="大模型口语化配音稿（默认开；--no-colloquial-openai 关闭）",
    )
    ap.add_argument(
        "--tts-polish-openai",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="大模型二次润色：适合 TTS 的标点与长度（默认开；--no-tts-polish-openai 关闭）",
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
        help=(
            "英文时间轴下：用 subtitle_reading_time 与 translate_srt 相同 CPS/over/under 判定；"
            "over 大模型压缩、under 大模型略充实口播（默认开，需 YOUTOBE_LLM / DeepSeek / OpenAI Key）"
        ),
    )
    ap.add_argument(
        "--dub-cps-target",
        type=float,
        default=None,
        help="口播密度目标（汉字/秒），默认读 YOUTOBE_DUB_CPS_TARGET 或 3.45",
    )
    ap.add_argument(
        "--emotion-align",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="按英文表达强度推断逐条韵律（Edge / ElevenLabs / Fish Speech 前缀；默认开，需大模型+英文时间轴）",
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
        emotion_align=args.emotion_align,
    )
    print(str(args.out_audio.resolve()))


if __name__ == "__main__":
    main()
