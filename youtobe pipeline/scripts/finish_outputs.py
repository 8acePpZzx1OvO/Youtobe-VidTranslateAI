#!/usr/bin/env python3
"""
【模块】finish_outputs.py — 在已有 raw/processed 素材上补跑：双语合并、配音、软字幕、硬烧成片（finalize-only 流程）。
【调用方】命令行；run.py --finalize-only 时子进程调用本脚本。

从已有素材补全「成片」：合并双语 SRT → 中文配音 → 软字幕 MP4 → 硬烧双语 MP4。
用于下载/翻译已成功，但合并、配音、封装未跑完的情况。

用法（在项目根目录）:
  python scripts/finish_outputs.py --stem oDaoz7hL0vQ
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


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


def _speed_basename_suffix(speed: float) -> str:
    t = f"{float(speed):.4g}".replace(".", "p")
    return f"_x{t}"


def main() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None  # type: ignore[misc, assignment]
    if load_dotenv is not None:
        fd = ROOT / "config" / "feature_defaults.env"
        if fd.is_file():
            load_dotenv(fd, override=False)
        load_dotenv(ROOT / ".env", override=True)

    try:
        import static_ffmpeg

        static_ffmpeg.add_paths()
    except ImportError:
        pass

    try:
        import pysrt  # noqa: F401
        from pydub import AudioSegment  # noqa: F401
    except ImportError as e:
        hint = ""
        em = str(e).lower()
        if "audioop" in em or "pyaudioop" in em:
            hint = (
                "\n说明: Python 3.13+ 已移除标准库 audioop，pydub 需额外安装:\n"
                "  pip install audioop-lts\n"
                "（已写入 requirements.txt，重新 pip install -r requirements.txt 即可）\n"
            )
        print(
            "缺少配音依赖（pydub / pysrt）。请在项目根执行:\n"
            "  pip install -r requirements.txt\n"
            "并使用同一解释器运行本脚本（推荐 .venv\\Scripts\\python.exe）。\n"
            f"原始错误: {e}\n"
            f"{hint}",
            file=sys.stderr,
        )
        sys.exit(3)

    ap = argparse.ArgumentParser(description="从已有 mp4 + en/zh SRT 补全配音与成片")
    ap.add_argument("--stem", required=True, help="视频 ID（与文件名 stem 一致）")
    ap.add_argument(
        "--raw-dir",
        type=Path,
        default=ROOT / "output" / "raw",
    )
    ap.add_argument(
        "--proc-dir",
        type=Path,
        default=ROOT / "output" / "processed",
    )
    ap.add_argument("--dub-voice", default="zh-CN-YunxiNeural")
    ap.add_argument("--dub-concurrency", type=int, default=5)
    ap.add_argument(
        "--no-soft-subs",
        action="store_true",
        help="不生成软字幕封装版（仍生成硬烧版）",
    )
    ap.add_argument(
        "--skip-dub",
        action="store_true",
        help="若已有 dub_zh.m4a 则跳过配音（仍重新封装）",
    )
    ap.add_argument(
        "--allow-incomplete-zh",
        action="store_true",
        help="允许中文字幕条数少于英文（默认会拒绝并提示先 --resume 续译）",
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
        help="配音：大模型口语化（默认开；--no-dub-colloquial-openai 关闭）",
    )
    ap.add_argument(
        "--dub-tts-polish-openai",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="配音：大模型 TTS 润色（默认开；--no-dub-tts-polish-openai 关闭）",
    )
    ap.add_argument(
        "--dub-max-speedup",
        type=float,
        default=None,
        help="配音对齐最大变速倍数（可选，默认 dub_zh 读环境变量）",
    )
    ap.add_argument(
        "--dub-edge-rate",
        default=None,
        metavar="PCT",
        help="Edge 语速，如 -5%%（可选）",
    )
    ap.add_argument(
        "--dub-edge-pitch",
        default="+0Hz",
        help="Edge 音高",
    )
    ap.add_argument(
        "--dub-sync-en-time",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="配音与英文字幕时间轴对齐（默认开）",
    )
    ap.add_argument(
        "--dub-duration-fit-openai",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="subtitle_reading_time 判定 over/under，大模型压缩/充实口播（默认开，需 LLM Key）",
    )
    ap.add_argument(
        "--dub-emotion-align",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="按英文表达强度推断逐条韵律（同 dub_zh --emotion-align，需大模型 Key）",
    )
    ap.add_argument(
        "--dub-cps-target",
        type=float,
        default=None,
        help="口播 CPS（与 subtitle_reading_time 一致），可选",
    )
    ap.add_argument(
        "--dub-en-srt",
        type=Path,
        default=None,
        help="显式英文 SRT 路径（可选）",
    )
    ap.add_argument(
        "--dub-backend",
        choices=("edge", "volc", "elevenlabs", "fish", "auto"),
        default="auto",
        help="配音：auto / edge / volc / elevenlabs / fish（Fish Speech HTTP）",
    )
    ap.add_argument(
        "--volc-format",
        default="wav",
        choices=("wav", "mp3", "aac"),
    )
    ap.add_argument("--volc-sample-rate", type=int, default=24000)
    ap.add_argument(
        "--video-speed",
        type=float,
        default=_default_video_speed(),
        metavar="N",
        help=(
            "成片生成后，再导出整片倍速 MP4（不修改 SRT）。"
            "软字幕源会生成无字幕轨的倍速版；硬烧成片字幕在画面内。"
            "默认 1.0；可用 YOUTOBE_VIDEO_SPEED。"
        ),
    )
    ap.add_argument(
        "--keep-intermediate",
        action="store_true",
        help="不精简 processed/raw（默认成功出硬烧片后会删除中间产物，仅留 en/zh SRT 与硬烧成片）",
    )
    args = ap.parse_args()

    stem = args.stem.strip()
    from youtobe_layout import (  # noqa: E402
        minimize_outputs_enabled,
        minimize_proc_dir,
        minimize_raw_dir,
        resolve_proc_base,
        resolve_raw_video,
        resolve_raw_vtt,
    )

    video = resolve_raw_video(args.raw_dir, stem)
    if not video:
        nested = args.raw_dir / stem / f"{stem}.mp4"
        flat = args.raw_dir / f"{stem}.mp4"
        print(f"缺少原始视频。已查找:\n  {nested}\n  {flat}", file=sys.stderr)
        print("请先完成下载，或将 mp4 放到上述路径之一。", file=sys.stderr)
        sys.exit(2)

    vtt_path = resolve_raw_vtt(args.raw_dir, stem)
    proc_base = resolve_proc_base(args.proc_dir, stem)
    proc_base.mkdir(parents=True, exist_ok=True)

    en_srt = proc_base / f"{stem}.en.srt"
    zh_srt = proc_base / f"{stem}.zh.srt"
    bi_srt = proc_base / f"{stem}.bilingual.srt"
    dub_audio = proc_base / f"{stem}.dub_zh.m4a"
    soft_out = proc_base / f"{stem}_zh_dub_softsubs.mp4"
    hard_out = proc_base / f"{stem}_zh_dub_hard_bilingual.mp4"
    temp_av = proc_base / f"{stem}_temp_av_for_burn.mp4"

    if not en_srt.exists() and vtt_path is not None and vtt_path.exists():
        print("从 VTT 生成英文 SRT…", file=sys.stderr)
        _run("vtt_to_srt.py", str(vtt_path), str(en_srt))
    if not en_srt.exists():
        print(f"缺少英文字幕: {en_srt}", file=sys.stderr)
        sys.exit(2)
    if not zh_srt.exists():
        print(f"缺少中文字幕: {zh_srt}，请先运行 run.py 完成翻译。", file=sys.stderr)
        sys.exit(2)

    import pysrt as _ps

    n_en = len(_ps.open(str(en_srt)))
    n_zh = len(_ps.open(str(zh_srt)))
    if n_zh < n_en and not args.allow_incomplete_zh:
        print(
            f"\n错误: 中文字幕仅 {n_zh} 条，英文 {n_en} 条，翻译未完成，不能生成完整配音与双语成片。\n"
            f"请在项目根续译（会接着已有 zh 往后译）:\n"
            f'  python scripts/translate_srt.py "{en_srt}" "{zh_srt}" --resume --engine smart\n'
            f"或带 URL 全流程:\n"
            f'  python run.py "https://youtu.be/{stem}" --resume\n'
            f"若仍坚持在当前残缺 zh 上出片，请加: --allow-incomplete-zh\n",
            file=sys.stderr,
        )
        sys.exit(4)

    zh_dubsync = proc_base / f"{stem}.zh.dubsync.srt"

    if args.skip_dub and dub_audio.exists():
        print("跳过配音（已存在 dub_zh.m4a）", file=sys.stderr)
    else:
        print("生成中文配音…", file=sys.stderr)
        dub_extra: list[str] = []
        if not args.dub_merge_repeats:
            dub_extra.append("--no-merge-repeats")
        if not args.dub_colloquial_openai:
            dub_extra.append("--no-colloquial-openai")
        if not args.dub_tts_polish_openai:
            dub_extra.append("--no-tts-polish-openai")
        dub_extra.extend(["--backend", args.dub_backend])
        if args.dub_backend in ("volc", "auto"):
            dub_extra.extend(["--volc-format", args.volc_format])
            dub_extra.extend(["--volc-sample-rate", str(args.volc_sample_rate)])
        if args.dub_max_speedup is not None:
            dub_extra.extend(["--max-speedup", str(args.dub_max_speedup)])
        if args.dub_edge_rate is not None:
            dub_extra.extend(["--edge-rate", args.dub_edge_rate])
        dub_extra.extend(["--edge-pitch", args.dub_edge_pitch])
        if not args.dub_sync_en_time:
            dub_extra.append("--no-sync-en-time")
        if not args.dub_duration_fit_openai:
            dub_extra.append("--no-duration-fit-openai")
        if not args.dub_emotion_align:
            dub_extra.append("--no-emotion-align")
        if args.dub_cps_target is not None:
            dub_extra.extend(["--dub-cps-target", str(args.dub_cps_target)])
        if args.dub_en_srt is not None:
            dub_extra.extend(["--en-srt", str(args.dub_en_srt)])
        _run(
            "dub_zh.py",
            str(video),
            str(zh_srt),
            str(dub_audio),
            "--voice",
            args.dub_voice,
            "--concurrency",
            str(args.dub_concurrency),
            *dub_extra,
        )

    zh_for_bi = zh_dubsync if zh_dubsync.exists() else zh_srt
    if zh_for_bi != zh_srt:
        print(f"合并双语: 使用口播对齐中文稿 {zh_for_bi.name}", file=sys.stderr)
    print("合并双语 SRT…", file=sys.stderr)
    _run("merge_bilingual_srt.py", str(en_srt), str(zh_for_bi), str(bi_srt))

    if not args.no_soft_subs:
        print("封装软字幕成片…", file=sys.stderr)
        _run(
            "mux_dub_subs.py",
            str(video),
            str(dub_audio),
            str(soft_out),
            "--subs",
            str(bi_srt),
        )

    print("生成硬烧双语 + 中文配音成片（任意播放器可见字幕）…", file=sys.stderr)
    _run("mux_dub_subs.py", str(video), str(dub_audio), str(temp_av))
    _run("burn_subtitles.py", str(temp_av), str(bi_srt), str(hard_out))
    temp_av.unlink(missing_ok=True)

    vs = float(args.video_speed)
    if not (0.25 <= vs <= 4.0):
        print("错误: --video-speed 须在 0.25–4.0 之间（默认 1.0）。", file=sys.stderr)
        sys.exit(2)
    if abs(vs - 1.0) >= 1e-9:
        sfx = _speed_basename_suffix(vs)
        dst_hard = hard_out.with_name(f"{hard_out.stem}{sfx}{hard_out.suffix}")
        print(f"成片再倍速 {vs}×（硬烧双语）→ {dst_hard.name}", file=sys.stderr)
        _run(
            "apply_video_playback_speed.py",
            "--mode",
            "rendered",
            "--input",
            str(hard_out),
            "--output",
            str(dst_hard),
            "--speed",
            str(vs),
        )
        if not args.no_soft_subs and soft_out.exists():
            dst_soft = soft_out.with_name(f"{soft_out.stem}{sfx}{soft_out.suffix}")
            print(
                f"成片再倍速 {vs}×（软字幕源；输出无字幕轨）→ {dst_soft.name}",
                file=sys.stderr,
            )
            _run(
                "apply_video_playback_speed.py",
                "--mode",
                "rendered",
                "--input",
                str(soft_out),
                "--output",
                str(dst_soft),
                "--speed",
                str(vs),
            )

    if (
        hard_out.exists()
        and not args.keep_intermediate
        and minimize_outputs_enabled()
    ):
        minimize_raw_dir(args.raw_dir, stem)
        minimize_proc_dir(proc_base, stem)
        print(
            "\n已精简目录：raw 仅保留原 mp4；processed 仅保留 en/zh SRT 与硬烧双语配音成片（及倍速副本）。"
            "调试请加 --keep-intermediate 或设 YOUTOBE_MINIMIZE_OUTPUTS=0。",
            file=sys.stderr,
        )

    print("\n完成。主要输出：", file=sys.stderr)
    if not args.no_soft_subs and soft_out.exists():
        print(f"  软字幕版: {soft_out}", file=sys.stderr)
    print(f"  硬字幕双语版（推荐）: {hard_out}", file=sys.stderr)
    if bi_srt.exists():
        print(f"  双语 SRT: {bi_srt}", file=sys.stderr)
    if dub_audio.exists():
        print(f"  中文配音轨: {dub_audio}", file=sys.stderr)
    if abs(float(args.video_speed) - 1.0) >= 1e-9:
        sfx = _speed_basename_suffix(float(args.video_speed))
        print(
            f"  倍速成片(硬烧): {hard_out.with_name(f'{hard_out.stem}{sfx}{hard_out.suffix}')}",
            file=sys.stderr,
        )
        if not args.no_soft_subs and soft_out.exists():
            print(
                f"  倍速成片(无软字幕轨): "
                f"{soft_out.with_name(f'{soft_out.stem}{sfx}{soft_out.suffix}')}",
                file=sys.stderr,
            )
    print(str(hard_out.resolve()))


if __name__ == "__main__":
    main()
