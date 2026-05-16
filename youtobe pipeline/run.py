#!/usr/bin/env python3
"""
【模块】run.py — YouTube 译配一键编排（下载、VTT→SRT、翻译、双语、配音、软/硬成片与可选倍速导出）。
【调用方】命令行直接执行；通过 subprocess 调用 scripts/*.py（cwd 为 pipeline 根目录）。

一键：下载 YouTube 视频 → 英文字幕转 SRT → 英译中 → 双语字幕 →
中文配音 + 软字幕 MP4 +（默认）硬烧双语字幕 MP4。

可选 --video-speed：在译配成片（1.0×）生成后，再对成片 MP4 单独导出一份观看倍速版（不改动任何 SRT；默认 1.0 不导出）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
OUT_RAW = ROOT / "output" / "raw"
OUT_PROC = ROOT / "output" / "processed"
sys.path.insert(0, str(SCRIPTS))
from youtobe_layout import (  # noqa: E402
    ensure_ffmpeg_on_path,
    minimize_outputs_enabled,
    minimize_proc_dir,
    minimize_raw_dir,
    resolve_proc_base,
)


def _run(py: str, *args: str) -> None:
    cmd = [sys.executable, str(SCRIPTS / py), *args]
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        sys.exit(r.returncode)


def _default_video_speed() -> float:
    try:
        return float((os.getenv("YOUTOBE_VIDEO_SPEED") or "1.0").strip())
    except ValueError:
        return 1.0


def _default_dub_keep_background() -> bool:
    return (os.getenv("YOUTOBE_DUB_KEEP_ORIGINAL_BG", "").strip().lower() in ("1", "true", "yes", "on"))


def _speed_basename_suffix(speed: float) -> str:
    """文件名用倍速标记，如 1.25 → _x1p25（避免小数点歧义）。"""
    t = f"{float(speed):.4g}".replace(".", "p")
    return f"_x{t}"


def _load_pipeline_env() -> None:
    """先载入 config/feature_defaults.env（不覆盖已有环境变量），再用 .env 覆盖同名键。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    fd = ROOT / "config" / "feature_defaults.env"
    if fd.is_file():
        load_dotenv(fd, override=False)
    load_dotenv(ROOT / ".env", override=True)


def main() -> None:
    _load_pipeline_env()

    ensure_ffmpeg_on_path()

    ap = argparse.ArgumentParser(description="YouTube 下载 + 英译中 + 双语字幕 + 中文配音")
    ap.add_argument(
        "url",
        nargs="?",
        default="",
        help="YouTube 链接（使用 --finalize-only 时可省略）",
    )
    ap.add_argument(
        "--raw-dir",
        type=Path,
        default=OUT_RAW,
        help="原始根目录（每视频子目录 raw/<视频ID>/）",
    )
    ap.add_argument(
        "--proc-dir",
        type=Path,
        default=OUT_PROC,
        help="处理后根目录（每视频子目录 processed/<视频ID>/）",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=0.25,
        help="翻译每条字幕间隔（秒），防限流",
    )
    ap.add_argument(
        "--bilingual",
        action="store_true",
        help="生成中英双语 SRT",
    )
    ap.add_argument(
        "--dub-zh",
        action="store_true",
        help="生成中文配音轨并与视频封装（仅中文音频，无原声）",
    )
    ap.add_argument(
        "--dub-voice",
        default="zh-CN-YunxiNeural",
        help="配音音色：Edge 如 zh-CN-YunxiNeural；火山如 zh_female_qingxin；Fish 可填 reference_id（见 env.example）",
    )
    ap.add_argument(
        "--dub-concurrency",
        type=int,
        default=5,
        help="配音并发数（Edge 默认 5；火山建议 2–4）",
    )
    ap.add_argument(
        "--translate-engine",
        default="smart",
        help=(
            "字幕翻译：smart 按 .env（DeepSeek+DeepL/Azure 混合 > 单 DeepL/Azure/DeepSeek > OpenAI > 阿里云/腾讯云 > Google）；"
            "可强制 deepl|azure|deepseek|openai|aliyun|tencent|google|mymemory"
        ),
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="翻译阶段断点续译（已有部分 zh.srt 时继续）",
    )
    ap.add_argument(
        "--batch-lines",
        type=int,
        default=22,
        help="翻译每批字幕条数（批量 API）",
    )
    ap.add_argument(
        "--batch-chars",
        type=int,
        default=6500,
        help="翻译每批最大字符数（约）",
    )
    ap.add_argument(
        "--no-soft-subs",
        action="store_true",
        help="配音成片不封装软字幕（仍会生成 .bilingual.srt 供外挂）",
    )
    ap.add_argument(
        "--burn",
        action="store_true",
        help="烧录中文字幕到原始视频（需 ffmpeg）",
    )
    ap.add_argument(
        "--burn-bilingual",
        action="store_true",
        help="烧录双语硬字幕到原始视频（需 --bilingual）",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="等价于 --bilingual --dub-zh，并默认生成硬烧双语+中文配音成片",
    )
    ap.add_argument(
        "--finalize-only",
        metavar="VIDEO_ID",
        default=None,
        help="跳过下载与翻译，仅根据 raw/<id>/<id>.mp4 与 processed/<id>/ 下 en/zh SRT 补全成片（兼容旧版扁平 raw/<id>.mp4）",
    )
    ap.add_argument(
        "--no-hard-bilingual",
        action="store_true",
        help="与 --full/--dub-zh 联用时不生成硬烧双语成片（仅软字幕封装，若成功）",
    )
    ap.add_argument(
        "--allow-incomplete-zh",
        action="store_true",
        help="中文字幕条数少于英文时仍合并/配音（默认 finalize 会拒绝；全流程也适用）",
    )
    ap.add_argument(
        "--speech-smooth",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="翻译阶段口语顺化（默认开；关：--no-speech-smooth；需大模型 Key 时走 LLM 顺化）",
    )
    ap.add_argument(
        "--translate-reading-align",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="翻译后对齐中文预估朗读时间与英文字幕槽位（默认开；需大模型 Key 才自动改写）",
    )
    ap.add_argument(
        "--dub-merge-repeats",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="配音：合并相邻重复/相似句（默认开；--no-dub-merge-repeats 关闭）",
    )
    ap.add_argument(
        "--dub-colloquial-openai",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="配音：大模型口语化旁白（默认开；关：--no-dub-colloquial-openai）",
    )
    ap.add_argument(
        "--dub-tts-polish-openai",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="配音：大模型 TTS 标点/断句润色（默认开；关：--no-dub-tts-polish-openai）",
    )
    ap.add_argument(
        "--dub-max-speedup",
        type=float,
        default=None,
        help="配音对齐时最大变速倍数（默认读 YOUTOBE_DUB_MAX_SPEEDUP 或 dub_zh 内建 1.22）",
    )
    ap.add_argument(
        "--dub-edge-rate",
        default=None,
        metavar="PCT",
        help="Edge 语速，如 -5%%（默认读 YOUTOBE_DUB_EDGE_RATE 或 -5%%）",
    )
    ap.add_argument(
        "--dub-edge-pitch",
        default="+0Hz",
        help="Edge 音高，如 +0Hz",
    )
    ap.add_argument(
        "--dub-sync-en-time",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="配音与英文字幕时间轴对齐（默认开；需同目录 .en.srt，与硬烧双语一致）",
    )
    ap.add_argument(
        "--dub-duration-fit-openai",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "英文轴下：subtitle_reading_time 判定 over/under，大模型压缩过长口播并充实过短口播（默认开，需 LLM Key）"
        ),
    )
    ap.add_argument(
        "--dub-emotion-align",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="配音按英文表达强度推断逐条韵律（Edge / ElevenLabs / Fish Speech，默认开，需大模型 Key）",
    )
    ap.add_argument(
        "--dub-cps-target",
        type=float,
        default=None,
        help="中文口播 CPS（与 subtitle_reading_time 一致），默认读 YOUTOBE_DUB_CPS_TARGET",
    )
    ap.add_argument(
        "--dub-en-srt",
        type=Path,
        default=None,
        help="显式指定英文 SRT 路径（可选；默认同 stem.en.srt）",
    )
    ap.add_argument(
        "--dub-backend",
        choices=("edge", "volc", "elevenlabs", "fish", "auto"),
        default="auto",
        help="配音：auto=（YOUTOBE_DUB_PREFER_FISH_SPEECH=1 且 Fish HTTP 可用时 fish）否则 火山>ElevenLabs>Edge",
    )
    ap.add_argument(
        "--volc-format",
        default="wav",
        choices=("wav", "mp3", "aac"),
        help="火山 TTS 格式（--dub-backend volc 或 auto 选用火山时传给 dub_zh.py）",
    )
    ap.add_argument(
        "--volc-sample-rate",
        type=int,
        default=24000,
        help="火山 TTS 采样率（Hz）",
    )
    ap.add_argument(
        "--asr-whisper",
        action="store_true",
        help="无 YouTube 字幕时用 faster-whisper 从视频识别英文（需 requirements-pro.txt）",
    )
    ap.add_argument(
        "--whisper-model",
        default="small",
        help="与 --asr-whisper 联用：faster-whisper 模型名",
    )
    ap.add_argument(
        "--video-speed",
        type=float,
        default=_default_video_speed(),
        metavar="N",
        help=(
            "译配流程按 1.0× 完成后，再对成片 MP4 导出一份整片倍速副本（setpts+atempo），"
            "不修改 en/zh/双语 SRT。1.0=不导出倍速版（默认）。"
            "软字幕成片倍速版会去掉字幕轨（画面与配音同倍速）；硬烧成片字幕在画面内无此问题。"
            "环境变量 YOUTOBE_VIDEO_SPEED。"
        ),
    )
    ap.add_argument(
        "--keep-intermediate",
        action="store_true",
        help=(
            "保留 raw 内 vtt/json 等下载附属文件，以及 processed 内双语 SRT、配音 m4a、"
            "软字幕成片等中间产物（默认在硬烧成片成功后会精简目录，便于搬运归档）"
        ),
    )
    ap.add_argument(
        "--dub-keep-background",
        action=argparse.BooleanOptionalAction,
        default=_default_dub_keep_background(),
        help=(
            "成片音轨：保留原片声音 + 口播时段 duck 压低 + 中文 TTS（默认 duck，见 YOUTOBE_DUB_BG_MODE）；"
            "关：--no-dub-keep-background；默认读 YOUTOBE_DUB_KEEP_ORIGINAL_BG。"
        ),
    )

    args = ap.parse_args()

    if args.finalize_only:
        fo: list[str] = [
            "finish_outputs.py",
            "--stem",
            args.finalize_only.strip(),
            "--raw-dir",
            str(args.raw_dir),
            "--proc-dir",
            str(args.proc_dir),
            "--dub-voice",
            args.dub_voice,
            "--dub-concurrency",
            str(args.dub_concurrency),
        ]
        if args.no_soft_subs:
            fo.append("--no-soft-subs")
        if args.allow_incomplete_zh:
            fo.append("--allow-incomplete-zh")
        if not args.dub_merge_repeats:
            fo.append("--no-dub-merge-repeats")
        if not args.dub_colloquial_openai:
            fo.append("--no-dub-colloquial-openai")
        if not args.dub_tts_polish_openai:
            fo.append("--no-dub-tts-polish-openai")
        fo.extend(["--dub-backend", args.dub_backend])
        fo.extend(["--volc-format", args.volc_format])
        fo.extend(["--volc-sample-rate", str(args.volc_sample_rate)])
        if args.dub_max_speedup is not None:
            fo.extend(["--dub-max-speedup", str(args.dub_max_speedup)])
        if args.dub_edge_rate is not None:
            fo.extend(["--dub-edge-rate", args.dub_edge_rate])
        fo.extend(["--dub-edge-pitch", args.dub_edge_pitch])
        if not args.dub_sync_en_time:
            fo.append("--no-dub-sync-en-time")
        if not args.dub_duration_fit_openai:
            fo.append("--no-dub-duration-fit-openai")
        if not args.dub_emotion_align:
            fo.append("--no-dub-emotion-align")
        if args.dub_cps_target is not None:
            fo.extend(["--dub-cps-target", str(args.dub_cps_target)])
        if args.dub_en_srt is not None:
            fo.extend(["--dub-en-srt", str(args.dub_en_srt)])
        fo.extend(["--video-speed", str(args.video_speed)])
        if args.keep_intermediate:
            fo.append("--keep-intermediate")
        if args.dub_keep_background:
            fo.append("--dub-keep-background")
        else:
            fo.append("--no-dub-keep-background")
        _run(*fo)
        return

    if not args.url.strip():
        print("请提供 YouTube 链接，或使用 --finalize-only <视频ID>", file=sys.stderr)
        sys.exit(2)

    vs = float(args.video_speed)
    if not (0.25 <= vs <= 4.0):
        print("错误: --video-speed 须在 0.25–4.0 之间（默认 1.0）。", file=sys.stderr)
        sys.exit(2)

    if args.full:
        args.bilingual = True
        args.dub_zh = True

    args.raw_dir.mkdir(parents=True, exist_ok=True)
    args.proc_dir.mkdir(parents=True, exist_ok=True)

    from download import download as dl  # type: ignore

    info = dl(args.url.strip(), args.raw_dir)
    print(json.dumps(info, ensure_ascii=False, indent=2))

    video = Path(info["video_path"])
    vtt = info.get("subtitle_path")
    stem = video.stem
    proc_base = resolve_proc_base(args.proc_dir, stem)
    proc_base.mkdir(parents=True, exist_ok=True)
    en_srt = proc_base / f"{stem}.en.srt"
    zh_srt = proc_base / f"{stem}.zh.srt"
    bi_srt = proc_base / f"{stem}.bilingual.srt"
    out_mp4 = proc_base / f"{stem}.zh_subs.mp4"
    out_bi_mp4 = proc_base / f"{stem}.bilingual_subs.mp4"
    dub_audio = proc_base / f"{stem}.dub_zh.m4a"
    final_dub = proc_base / f"{stem}_zh_dub_softsubs.mp4"
    hard_dub = proc_base / f"{stem}_zh_dub_hard_bilingual.mp4"
    temp_av = proc_base / f"{stem}_temp_av_for_burn.mp4"

    if vtt:
        _run("vtt_to_srt.py", str(Path(vtt)), str(en_srt))
    elif args.asr_whisper:
        print(
            "未获取 YouTube 字幕，使用本地 faster-whisper 识别英文（需 requirements-pro.txt）…",
            file=sys.stderr,
        )
        _run(
            "asr_en_srt.py",
            str(video),
            str(en_srt),
            "--model",
            args.whisper_model,
        )
    else:
        ex = ["python", "run.py", f'"{args.url.strip()}"']
        if args.full:
            ex.append("--full")
        elif args.dub_zh:
            ex.append("--dub-zh")
        elif args.bilingual:
            ex.append("--bilingual")
        ex.append("--asr-whisper")
        vs = float(args.video_speed)
        if abs(vs - 1.0) >= 1e-9:
            ex.extend(["--video-speed", str(vs)])
        if args.whisper_model != "small":
            ex.extend(["--whisper-model", args.whisper_model])
        print(
            "未获取到英文字幕，无法自动翻译。\n"
            "该视频可能没有创作者字幕或自动字幕，或当前 yt-dlp 请求的语言未命中。\n"
            "可选方案：\n"
            "  • 本地英文语音识别（需安装 requirements-pro.txt / faster-whisper）：\n"
            f"      {' '.join(ex)}\n"
            "  • 或自行将英文字幕 .vtt / .srt 放到 raw/<视频ID>/ 下后重跑；或换有字幕的视频。\n",
            file=sys.stderr,
        )
        sys.exit(2)

    t_args: list[str] = [
        "translate_srt.py",
        str(en_srt),
        str(zh_srt),
        "--sleep",
        str(args.sleep),
        "--engine",
        args.translate_engine,
        "--batch-lines",
        str(args.batch_lines),
        "--batch-chars",
        str(args.batch_chars),
    ]
    if args.resume:
        t_args.append("--resume")
    if not args.speech_smooth:
        t_args.append("--no-speech-smooth")
    if not args.translate_reading_align:
        t_args.append("--no-reading-time-align")
    _run(*t_args)

    if (args.dub_zh or args.full or args.bilingual) and not args.allow_incomplete_zh:
        import pysrt as _ps

        _ne = len(_ps.open(str(en_srt)))
        _nz = len(_ps.open(str(zh_srt)))
        if _nz < _ne:
            print(
                f"\n错误: 中文字幕 {_nz} 条 < 英文 {_ne} 条，翻译未完成。\n"
                f"请续译后重试:\n"
                f'  python scripts/translate_srt.py "{en_srt}" "{zh_srt}" --resume --engine smart\n'
                f"或: python run.py \"{args.url.strip()}\" --full --resume\n"
                f"若坚持在残缺 zh 上出片，请加 --allow-incomplete-zh\n",
                file=sys.stderr,
            )
            sys.exit(4)

    if args.dub_zh:
        dub_x: list[str] = []
        if not args.dub_merge_repeats:
            dub_x.append("--no-merge-repeats")
        if not args.dub_colloquial_openai:
            dub_x.append("--no-colloquial-openai")
        if not args.dub_tts_polish_openai:
            dub_x.append("--no-tts-polish-openai")
        dub_x.extend(["--backend", args.dub_backend])
        if args.dub_backend in ("volc", "auto"):
            dub_x.extend(["--volc-format", args.volc_format])
            dub_x.extend(["--volc-sample-rate", str(args.volc_sample_rate)])
        if args.dub_max_speedup is not None:
            dub_x.extend(["--max-speedup", str(args.dub_max_speedup)])
        if args.dub_edge_rate is not None:
            dub_x.extend(["--edge-rate", args.dub_edge_rate])
        dub_x.extend(["--edge-pitch", args.dub_edge_pitch])
        if not args.dub_sync_en_time:
            dub_x.append("--no-sync-en-time")
        if not args.dub_duration_fit_openai:
            dub_x.append("--no-duration-fit-openai")
        if not args.dub_emotion_align:
            dub_x.append("--no-emotion-align")
        if args.dub_cps_target is not None:
            dub_x.extend(["--dub-cps-target", str(args.dub_cps_target)])
        if args.dub_en_srt is not None:
            dub_x.extend(["--en-srt", str(args.dub_en_srt)])
        _run(
            "dub_zh.py",
            str(video),
            str(zh_srt),
            str(dub_audio),
            "--voice",
            args.dub_voice,
            "--concurrency",
            str(args.dub_concurrency),
            *dub_x,
        )
        mux_audio = dub_audio
        if args.dub_keep_background:
            mixed = proc_base / f"{stem}.dub_zh_bgmix.m4a"
            print(
                "配音混音: 保留原片音轨 + 口播时段压低 + 中文 TTS …",
                file=sys.stderr,
            )
            _run(
                "mix_dub_background.py",
                str(video),
                str(dub_audio),
                str(mixed),
                "--en-srt",
                str(en_srt),
            )
            mux_audio = mixed
        zh_dubsync = proc_base / f"{stem}.zh.dubsync.srt"
        zh_for_bi = zh_dubsync if zh_dubsync.exists() else zh_srt
        if zh_for_bi != zh_srt:
            print(
                f"合并双语字幕: 使用口播对齐中文稿 {zh_for_bi.name}",
                file=sys.stderr,
            )
        _run("merge_bilingual_srt.py", str(en_srt), str(zh_for_bi), str(bi_srt))
        if args.no_soft_subs:
            _run("mux_dub_subs.py", str(video), str(mux_audio), str(final_dub))
        else:
            _run(
                "mux_dub_subs.py",
                str(video),
                str(mux_audio),
                str(final_dub),
                "--subs",
                str(bi_srt),
            )
        if not args.no_hard_bilingual:
            _run("mux_dub_subs.py", str(video), str(mux_audio), str(temp_av))
            _run("burn_subtitles.py", str(temp_av), str(bi_srt), str(hard_dub))
            temp_av.unlink(missing_ok=True)

    elif args.bilingual or args.burn_bilingual:
        _run("merge_bilingual_srt.py", str(en_srt), str(zh_srt), str(bi_srt))

    if args.burn:
        _run("burn_subtitles.py", str(video), str(zh_srt), str(out_mp4))
    if args.burn_bilingual:
        if not bi_srt.exists():
            print("未找到双语字幕，无法烧录。", file=sys.stderr)
            sys.exit(1)
        _run("burn_subtitles.py", str(video), str(bi_srt), str(out_bi_mp4))

    if abs(vs - 1.0) >= 1e-9:
        sfx = _speed_basename_suffix(vs)
        if args.dub_zh:
            if not args.no_hard_bilingual and hard_dub.exists():
                dst = hard_dub.with_name(f"{hard_dub.stem}{sfx}{hard_dub.suffix}")
                print(
                    f"成片再倍速 {vs}×（硬烧双语）→ {dst.name}",
                    file=sys.stderr,
                )
                _run(
                    "apply_video_playback_speed.py",
                    "--mode",
                    "rendered",
                    "--input",
                    str(hard_dub),
                    "--output",
                    str(dst),
                    "--speed",
                    str(vs),
                )
            if not args.no_soft_subs and final_dub.exists():
                dst = final_dub.with_name(f"{final_dub.stem}{sfx}{final_dub.suffix}")
                print(
                    f"成片再倍速 {vs}×（软字幕源；输出无字幕轨，仅画面+配音同倍速）→ {dst.name}",
                    file=sys.stderr,
                )
                _run(
                    "apply_video_playback_speed.py",
                    "--mode",
                    "rendered",
                    "--input",
                    str(final_dub),
                    "--output",
                    str(dst),
                    "--speed",
                    str(vs),
                )
        if args.burn and out_mp4.exists():
            dst = out_mp4.with_name(f"{out_mp4.stem}{sfx}{out_mp4.suffix}")
            print(f"成片再倍速 {vs}×（硬烧中字）→ {dst.name}", file=sys.stderr)
            _run(
                "apply_video_playback_speed.py",
                "--mode",
                "rendered",
                "--input",
                str(out_mp4),
                "--output",
                str(dst),
                "--speed",
                str(vs),
            )
        if args.burn_bilingual and out_bi_mp4.exists():
            dst = out_bi_mp4.with_name(f"{out_bi_mp4.stem}{sfx}{out_bi_mp4.suffix}")
            print(f"成片再倍速 {vs}×（硬烧双语/原片音轨）→ {dst.name}", file=sys.stderr)
            _run(
                "apply_video_playback_speed.py",
                "--mode",
                "rendered",
                "--input",
                str(out_bi_mp4),
                "--output",
                str(dst),
                "--speed",
                str(vs),
            )

    if (
        (args.dub_zh or args.full)
        and hard_dub.exists()
        and not args.no_hard_bilingual
        and not args.keep_intermediate
        and minimize_outputs_enabled()
    ):
        minimize_raw_dir(args.raw_dir, stem)
        minimize_proc_dir(proc_base, stem)
        print(
            "\n已精简目录：raw 仅保留原 mp4；processed 仅保留 en/zh SRT 与硬烧双语配音成片（及倍速副本）。"
            "调试请用 --keep-intermediate 或 YOUTOBE_MINIMIZE_OUTPUTS=0。",
            file=sys.stderr,
        )

    print("\n完成。主要输出：")
    print(f"  原始视频: {video}")
    print(f"  英文 SRT: {en_srt}")
    print(f"  中文 SRT: {zh_srt}")
    if args.bilingual or args.dub_zh or args.full:
        if bi_srt.exists():
            print(f"  双语 SRT: {bi_srt}")
    if args.dub_zh:
        if dub_audio.exists():
            print(f"  中文配音(仅音频): {dub_audio}")
        if not args.no_soft_subs and final_dub.exists():
            print(f"  成片(软字幕，播放器需支持): {final_dub}")
        if not args.no_hard_bilingual and hard_dub.exists():
            print(f"  成片(硬烧双语+中文配音，推荐): {hard_dub}")
    if args.burn:
        print(f"  硬字幕(中) 基于原片: {out_mp4}")
    if args.burn_bilingual:
        print(f"  硬字幕(双语) 基于原片: {out_bi_mp4}")
    if abs(vs - 1.0) >= 1e-9:
        sfx = _speed_basename_suffix(vs)
        if args.dub_zh:
            if not args.no_hard_bilingual and hard_dub.exists():
                print(
                    f"  倍速成片(硬烧，推荐): "
                    f'{hard_dub.with_name(f"{hard_dub.stem}{sfx}{hard_dub.suffix}")}'
                )
            if not args.no_soft_subs and final_dub.exists():
                print(
                    f"  倍速成片(无软字幕轨): "
                    f'{final_dub.with_name(f"{final_dub.stem}{sfx}{final_dub.suffix}")}'
                )
        if args.burn and out_mp4.exists():
            print(
                f"  倍速成片(硬烧中字): "
                f'{out_mp4.with_name(f"{out_mp4.stem}{sfx}{out_mp4.suffix}")}'
            )
        if args.burn_bilingual and out_bi_mp4.exists():
            print(
                f"  倍速成片(硬烧双语): "
                f'{out_bi_mp4.with_name(f"{out_bi_mp4.stem}{sfx}{out_bi_mp4.suffix}")}'
            )


if __name__ == "__main__":
    main()
