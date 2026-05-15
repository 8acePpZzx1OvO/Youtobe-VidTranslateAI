#!/usr/bin/env python3
"""
英译中字幕：DeepL / Azure / OpenAI / 阿里云 / 腾讯云 等；否则 Google / MyMemory。
支持断点续译（--resume）、可配置批量大小。
--speech-smooth：在批次内按对话语境顺化口语（去口吃/填料）再译中文，条数与时间轴与英文 SRT 一致（需 OPENAI_API_KEY 时效果最佳）。
环境变量见 env.example。
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Callable

try:
    import pysrt
except ImportError:
    print("请先安装: pip install pysrt", file=sys.stderr)
    sys.exit(1)

try:
    from deep_translator import GoogleTranslator, MyMemoryTranslator
    from deep_translator.exceptions import TooManyRequests
except ImportError:
    print("请先安装: pip install deep-translator", file=sys.stderr)
    sys.exit(1)

from translation_clients import (
    aliyun_translate_batch,
    azure_translate_batch,
    deepl_translate_batch,
    openai_fluent_zh_batch,
    openai_translate_batch,
    tencent_translate_batch,
)


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return " ".join(s.split())


_FILLER_START = re.compile(
    r"^(?:(?:uh|um|er|ah+|hmm+|mmm*|oh+|well)\s*[,]?\s*)+",
    re.I,
)
_DISCOURSE_FILLERS = re.compile(
    r"\b(?:you know|i mean|sort of|kind of)\b",
    re.I,
)


def _local_smooth_one(s: str) -> str:
    """无大模型时：去掉句首语气词/常见英文填料，合并明显口吃式重复。"""
    t = s.strip()
    if not t:
        return t
    t = _FILLER_START.sub("", t).strip()
    t = _DISCOURSE_FILLERS.sub(" ", t)
    t = re.sub(r"\b(\w+)(\s+\1\b){2,}", r"\1", t, flags=re.I)
    return " ".join(t.split())


def _local_smooth_lines(lines: list[str]) -> list[str]:
    return [_local_smooth_one(x) for x in lines]


def _call_translate_line(
    fn: Callable[[str], str], text: str, *, label: str
) -> str:
    quick_sleep = 0.7
    for attempt in range(3):
        try:
            return fn(text).strip()
        except TooManyRequests:
            wait = 30.0 + random.uniform(0, 20) + attempt * 12
            print(f"{label} 限流，等待 {wait:.0f}s…", file=sys.stderr)
            time.sleep(wait)
        except Exception:
            time.sleep(quick_sleep * (attempt + 1))
    for attempt in range(12):
        try:
            return fn(text).strip()
        except TooManyRequests:
            wait = min(120.0, 35.0 + attempt * 10.0 + random.uniform(0, 15))
            print(f"{label} 仍限流，等待 {wait:.0f}s…", file=sys.stderr)
            time.sleep(wait)
        except Exception as e:
            raise RuntimeError(f"{label}: {e}") from e
    raise RuntimeError(f"{label}: 多次失败后放弃")


def _chunk_indices(
    n: int,
    start: int,
    *,
    max_lines: int,
    max_chars: int,
    texts: list[str],
) -> list[tuple[int, int]]:
    chunks: list[tuple[int, int]] = []
    i = start
    while i < n:
        j = i
        acc = 0
        lines = 0
        while j < n and lines < max_lines and acc + len(texts[j]) + 2 <= max_chars:
            acc += len(texts[j]) + 2
            lines += 1
            j += 1
        if j == i:
            j = i + 1
        chunks.append((i, j))
        i = j
    return chunks


def _resolve_engine(engine: str) -> str:
    e = engine.lower().strip()
    if e in ("auto", "smart", "best"):
        return "smart"
    return e


def _pick_env_batch_backend() -> tuple[str, Callable[[list[str]], list[str]]] | None:
    dk = os.getenv("DEEPL_API_KEY", "").strip()
    if dk:
        use_pro = os.getenv("DEEPL_USE_PRO", "").strip().lower() in ("1", "true", "yes")

        def deepl_fn(tx: list[str]) -> list[str]:
            return deepl_translate_batch(tx, api_key=dk, free_api=not use_pro)

        return ("DeepL", deepl_fn)

    mk = os.getenv("MICROSOFT_API_KEY", "").strip()
    if mk:
        region = os.getenv("AZURE_TRANSLATOR_REGION", "").strip() or None

        def azure_fn(tx: list[str]) -> list[str]:
            return azure_translate_batch(tx, api_key=mk, region=region)

        return ("Azure", azure_fn)

    ok = os.getenv("OPENAI_API_KEY", "").strip()
    if ok:

        def openai_fn(tx: list[str]) -> list[str]:
            return openai_translate_batch(tx, api_key=ok)

        return ("OpenAI", openai_fn)

    ak = (
        os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "").strip()
        or os.getenv("ALIYUN_ACCESS_KEY_ID", "").strip()
    )
    sk = (
        os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "").strip()
        or os.getenv("ALIYUN_ACCESS_KEY_SECRET", "").strip()
    )
    if ak and sk:
        rgn = os.getenv("ALIYUN_MT_REGION", "cn-hangzhou").strip() or "cn-hangzhou"

        def ali_fn(tx: list[str]) -> list[str]:
            return aliyun_translate_batch(
                tx,
                access_key_id=ak,
                access_key_secret=sk,
                region=rgn,
            )

        return ("阿里云机器翻译", ali_fn)

    sid = os.getenv("TENCENTCLOUD_SECRET_ID", "").strip()
    sek = os.getenv("TENCENTCLOUD_SECRET_KEY", "").strip()
    if sid and sek:
        rgn = os.getenv("TENCENT_TMT_REGION", "ap-beijing").strip() or "ap-beijing"
        pid_s = os.getenv("TENCENT_TMT_PROJECT_ID", "0").strip()
        try:
            pid = int(pid_s)
        except ValueError:
            pid = 0

        def tc_fn(tx: list[str]) -> list[str]:
            return tencent_translate_batch(
                tx,
                secret_id=sid,
                secret_key=sek,
                region=rgn,
                project_id=pid,
            )

        return ("腾讯云TMT", tc_fn)

    return None


def _resolve_batch_translator(
    eng: str,
) -> tuple[str | None, Callable[[list[str]], list[str]] | None]:
    """返回 (显示名, 批量函数)。无批量能力时 (None, None)。"""
    if eng == "google" or eng == "mymemory":
        return None, None

    if eng == "deepl":
        key = os.getenv("DEEPL_API_KEY", "").strip()
        if not key:
            print("错误: --engine deepl 需要 DEEPL_API_KEY", file=sys.stderr)
            sys.exit(3)
        use_pro = os.getenv("DEEPL_USE_PRO", "").strip().lower() in ("1", "true", "yes")

        def fn(tx: list[str]) -> list[str]:
            return deepl_translate_batch(tx, api_key=key, free_api=not use_pro)

        return "DeepL", fn

    if eng == "azure":
        mk = os.getenv("MICROSOFT_API_KEY", "").strip()
        if not mk:
            print("错误: --engine azure 需要 MICROSOFT_API_KEY", file=sys.stderr)
            sys.exit(3)
        region = os.getenv("AZURE_TRANSLATOR_REGION", "").strip() or None

        def fn2(tx: list[str]) -> list[str]:
            return azure_translate_batch(tx, api_key=mk, region=region)

        return "Azure", fn2

    if eng == "openai":
        ok = os.getenv("OPENAI_API_KEY", "").strip()
        if not ok:
            print("错误: --engine openai 需要 OPENAI_API_KEY", file=sys.stderr)
            sys.exit(3)

        def fn3(tx: list[str]) -> list[str]:
            return openai_translate_batch(tx, api_key=ok)

        return "OpenAI", fn3

    if eng == "aliyun":
        ak = (
            os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "").strip()
            or os.getenv("ALIYUN_ACCESS_KEY_ID", "").strip()
        )
        sk = (
            os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "").strip()
            or os.getenv("ALIYUN_ACCESS_KEY_SECRET", "").strip()
        )
        if not ak or not sk:
            print(
                "错误: --engine aliyun 需要 ALIBABA_CLOUD_ACCESS_KEY_ID / "
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET（或 ALIYUN_ACCESS_KEY_*）",
                file=sys.stderr,
            )
            sys.exit(3)
        rgn = os.getenv("ALIYUN_MT_REGION", "cn-hangzhou").strip() or "cn-hangzhou"

        def fn4(tx: list[str]) -> list[str]:
            return aliyun_translate_batch(
                tx, access_key_id=ak, access_key_secret=sk, region=rgn
            )

        return "阿里云机器翻译", fn4

    if eng == "tencent":
        sid = os.getenv("TENCENTCLOUD_SECRET_ID", "").strip()
        sek = os.getenv("TENCENTCLOUD_SECRET_KEY", "").strip()
        if not sid or not sek:
            print(
                "错误: --engine tencent 需要 TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY",
                file=sys.stderr,
            )
            sys.exit(3)
        rgn = os.getenv("TENCENT_TMT_REGION", "ap-beijing").strip() or "ap-beijing"
        try:
            pid = int(os.getenv("TENCENT_TMT_PROJECT_ID", "0").strip())
        except ValueError:
            pid = 0

        def fn5(tx: list[str]) -> list[str]:
            return tencent_translate_batch(
                tx,
                secret_id=sid,
                secret_key=sek,
                region=rgn,
                project_id=pid,
            )

        return "腾讯云TMT", fn5

    # smart：无任一云端 Key 时 _pick_env_batch_backend() 返回 None，需解包为 (None, None)
    picked = _pick_env_batch_backend()
    return picked if picked is not None else (None, None)


def translate_srt(
    src: Path,
    dst: Path,
    *,
    sleep_s: float = 0.12,
    engine: str = "smart",
    resume: bool = False,
    batch_lines: int = 22,
    batch_chars: int = 6500,
    speech_smooth: bool = False,
    dedupe_roll: bool = True,
    sentence_merge_en: bool = True,
) -> None:
    dedupe_roll_effective = dedupe_roll and not resume
    if resume and dedupe_roll:
        print(
            "提示: 续译（--resume）时跳过英文滚动去重，以免条数与已有中文错位。",
            "若需去重请去掉 --resume 并删除未完成的中文 SRT 后全量重译。",
            file=sys.stderr,
        )
    if dedupe_roll_effective:
        from rolling_caption_dedupe import dedupe_srt_file

        kept, removed = dedupe_srt_file(src)
        print(
            f"英文滚动字幕去重: 保留 {kept} 条，删去空/重复尾 {removed} 条（已写回 {src.name}）。",
            file=sys.stderr,
        )

    sm_effective = (
        sentence_merge_en
        and not resume
        and os.getenv("YOUTOBE_SENTENCE_MERGE_EN", "1").strip().lower()
        not in ("0", "false", "no", "off")
    )
    if resume and sentence_merge_en:
        print(
            "提示: 续译（--resume）时跳过「按英文句合并」以免条数与已有中文错位。",
            file=sys.stderr,
        )
    if sm_effective:
        from en_sentence_merge_srt import merge_en_srt_by_sentences

        n0, n1 = merge_en_srt_by_sentences(src)
        print(
            f"英文句合并: {n0} 条 → {n1} 条（按 . ? ! 断句，已写回 {src.name}）。",
            file=sys.stderr,
        )

    subs_en = list(pysrt.open(str(src)))
    n = len(subs_en)
    texts = [_clean(s.text) for s in subs_en]

    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    use_fluent_openai = bool(speech_smooth and openai_key)
    if speech_smooth and not use_fluent_openai:
        texts = _local_smooth_lines(texts)
        print(
            "口语顺化: 已做本地轻量去填料（设置 OPENAI_API_KEY 后可用语境合并+中译）",
            file=sys.stderr,
        )

    eng = _resolve_engine(engine)
    out = pysrt.SubRipFile()
    start_idx = 0

    if resume and dst.exists():
        try:
            prev = list(pysrt.open(str(dst)))
            if len(prev) >= n:
                print(
                    f"续译：{dst.name} 已完整（{len(prev)}/{n}），跳过翻译。",
                    file=sys.stderr,
                )
                return
            if len(prev) > 0:
                d0 = abs(prev[0].start.ordinal - subs_en[0].start.ordinal)
                if d0 <= 80:
                    start_idx = len(prev)
                    for p in prev:
                        out.append(p)
                    print(
                        f"续译：从第 {start_idx + 1} 条开始（已加载 {start_idx} 条）。",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "续译：已有文件与时间轴不匹配，将从头翻译。",
                        file=sys.stderr,
                    )
        except Exception as e:
            print(f"续译：读取旧文件失败，从头翻译: {e}", file=sys.stderr)

    if use_fluent_openai:

        def batch_fn(tx: list[str]) -> list[str]:
            return openai_fluent_zh_batch(tx, api_key=openai_key)

        batch_name = "OpenAI（语境顺化→中文）"
        use_batch = True
        print(
            "口语顺化: 使用 OpenAI 在批次内合并语境并输出中文（每批条数/字符已适度收紧）",
            file=sys.stderr,
        )
    else:
        batch_name, batch_fn = _resolve_batch_translator(eng)
        use_batch = batch_fn is not None

    eff_lines = min(batch_lines, 22) if use_fluent_openai else batch_lines
    eff_chars = min(batch_chars, 5500) if use_fluent_openai else batch_chars

    if use_batch:
        print(
            f"翻译后端: {batch_name}（批量，约每批 {eff_lines} 条）",
            file=sys.stderr,
        )
    elif eng == "smart":
        print(
            "翻译后端: Google / MyMemory（未检测到 DeepL/Azure/OpenAI/阿里云/腾讯云 Key）",
            file=sys.stderr,
        )

    if use_batch and batch_fn is not None:
        chunks = _chunk_indices(
            n,
            start_idx,
            max_lines=eff_lines,
            max_chars=eff_chars,
            texts=texts,
        )
        for a, b in chunks:
            slice_tx = texts[a:b]
            empty_idx = [k for k, t in enumerate(slice_tx) if not t]
            payload = [t if t else " " for t in slice_tx]
            try:
                zh_parts = batch_fn(payload)
            except Exception as e:
                print(f"批量翻译失败 [{a}:{b}]: {e}，本批改为逐条请求…", file=sys.stderr)
                zh_parts = []
                bf = batch_fn
                for t in payload:
                    if not t.strip():
                        zh_parts.append("")
                    else:
                        zh_parts.append(
                            _call_translate_line(
                                lambda x, _bf=bf: _bf([x])[0],
                                t,
                                label=batch_name or "API",
                            )
                        )
            for k in empty_idx:
                if k < len(zh_parts):
                    zh_parts[k] = ""
            if len(zh_parts) != b - a:
                raise RuntimeError("批量结果长度与字幕条数不一致")
            for j in range(a, b):
                zt = zh_parts[j - a].strip() or texts[j] or ""
                out.append(
                    pysrt.SubRipItem(j + 1, subs_en[j].start, subs_en[j].end, zt)
                )
            dst.parent.mkdir(parents=True, exist_ok=True)
            out.save(str(dst), encoding="utf-8")
            print(f"  已保存: {b}/{n}", file=sys.stderr)
            time.sleep(max(sleep_s, 0.04))
    else:
        google = GoogleTranslator(source="en", target="zh-CN")
        mymem = MyMemoryTranslator(source="english", target="chinese simplified")

        def via_g(t: str) -> str:
            return google.translate(t)

        def via_m(t: str) -> str:
            return mymem.translate(t)

        google_failed = eng == "mymemory"

        for j in range(start_idx, n):
            text = texts[j]
            if not text:
                out.append(
                    pysrt.SubRipItem(j + 1, subs_en[j].start, subs_en[j].end, "")
                )
            elif eng == "google":
                zh = _call_translate_line(via_g, text, label="Google")
                out.append(
                    pysrt.SubRipItem(j + 1, subs_en[j].start, subs_en[j].end, zh or text)
                )
            elif eng == "mymemory":
                zh = _call_translate_line(via_m, text, label="MyMemory")
                out.append(
                    pysrt.SubRipItem(j + 1, subs_en[j].start, subs_en[j].end, zh or text)
                )
            else:
                if not google_failed:
                    try:
                        zh = _call_translate_line(via_g, text, label="Google")
                    except RuntimeError:
                        print("Google 不可用，改用 MyMemory…", file=sys.stderr)
                        google_failed = True
                        zh = _call_translate_line(via_m, text, label="MyMemory")
                else:
                    zh = _call_translate_line(via_m, text, label="MyMemory")
                out.append(
                    pysrt.SubRipItem(j + 1, subs_en[j].start, subs_en[j].end, zh or text)
                )
            dst.parent.mkdir(parents=True, exist_ok=True)
            out.save(str(dst), encoding="utf-8")
            gap = max(sleep_s, 0.45) if (eng == "mymemory" or google_failed) else sleep_s
            time.sleep(gap)


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description="SRT 英译中（批量 API + 续译）")
    ap.add_argument("src", type=Path, help="英文 SRT")
    ap.add_argument("dst", type=Path, nargs="?", help="输出中文 SRT")
    ap.add_argument("--sleep", type=float, default=0.12, help="每批之间的间隔（秒）")
    ap.add_argument(
        "--engine",
        default="smart",
        help=(
            "smart: DeepL>Azure>OpenAI>阿里云>腾讯云>Google→MyMemory；"
            "deepl|azure|openai|aliyun|tencent|google|mymemory 强制单一后端"
        ),
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="目标已存在且未译满时从断点继续",
    )
    ap.add_argument("--batch-lines", type=int, default=22, help="每批最多条数")
    ap.add_argument("--batch-chars", type=int, default=6500, help="每批最大字符数（约）")
    ap.add_argument(
        "--speech-smooth",
        action="store_true",
        help=(
            "口语顺化：有 OPENAI_API_KEY 时用 OpenAI 在批次内合并语境并输出中文；"
            "否则先对英文做本地轻量去填料再按 --engine 翻译"
        ),
    )
    ap.add_argument(
        "--dedupe-roll",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="翻译前对英文 SRT 做滚动去重（默认开；与 --resume 不同时使用）",
    )
    ap.add_argument(
        "--sentence-merge-en",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="翻译前按英文句（. ? !）合并相邻条（默认开；续译时自动跳过）",
    )
    args = ap.parse_args()
    dst = (
        args.src.with_name(args.src.stem + ".zh.srt")
        if args.dst is None
        else args.dst
    )
    translate_srt(
        args.src,
        dst,
        sleep_s=args.sleep,
        engine=args.engine,
        resume=args.resume,
        batch_lines=args.batch_lines,
        batch_chars=args.batch_chars,
        speech_smooth=args.speech_smooth,
        dedupe_roll=args.dedupe_roll,
        sentence_merge_en=args.sentence_merge_en,
    )
    print(str(dst.resolve()))


if __name__ == "__main__":
    main()
