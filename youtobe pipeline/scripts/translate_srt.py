#!/usr/bin/env python3
"""
【模块】translate_srt.py — 字幕英译中、断点续译、口语顺化、翻译后「朗读时长↔英文槽位」对齐。
【调用方】命令行；run.py 子进程调用。

英译中字幕：DeepL / Azure / DeepSeek（可与 DeepL/Azure 混合）/ 兼容 Chat 的大模型（YOUTOBE_LLM / DeepSeek / 旧 OPENAI）/ 阿里云 / 腾讯云 等；否则 Google / MyMemory。
支持断点续译（--resume）、可配置批量大小。
smart：若同时配置大模型 Key 与 DEEPL（或 Azure），默认「机翻 + 大模型润色」混合；仅 DeepSeek 等则直译。
--speech-smooth：已配置任一可用大模型（YOUTOBE_LLM_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY）时用语境顺化→中文；否则仅本地轻量去填料。
翻译结束后（默认开）：按英文字幕条时长与中文预估朗读时间比对，对大模型可配环境时自动压缩/略扩中文，减轻配音吞字与句内过长留白（见 subtitle_reading_time.py）。
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
    deepseek_translate_batch,
    hybrid_mt_deepseek_batch,
    llm_configured,
    openai_fluent_zh_batch,
    openai_translate_batch,
    tencent_translate_batch,
    zh_reading_time_align_batch,
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


def _translation_hybrid_enabled() -> bool:
    v = (os.getenv("YOUTOBE_TRANSLATION_HYBRID") or "1").strip().lower()
    return v not in ("0", "false", "no", "off", "")


def _pick_env_batch_backend() -> tuple[str, Callable[[list[str]], list[str]]] | None:
    ds = os.getenv("DEEPSEEK_API_KEY", "").strip()
    hybrid = _translation_hybrid_enabled()
    dk = os.getenv("DEEPL_API_KEY", "").strip()
    mk = os.getenv("MICROSOFT_API_KEY", "").strip()

    if hybrid and llm_configured() and dk:
        use_pro = os.getenv("DEEPL_USE_PRO", "").strip().lower() in ("1", "true", "yes")

        def deepl_fn(tx: list[str]) -> list[str]:
            return deepl_translate_batch(tx, api_key=dk, free_api=not use_pro)

        def hybrid_deepl(tx: list[str]) -> list[str]:
            return hybrid_mt_deepseek_batch(tx, mt_batch_fn=deepl_fn, api_key=None)

        return ("DeepL+DeepSeek 混合", hybrid_deepl)

    if dk:
        use_pro = os.getenv("DEEPL_USE_PRO", "").strip().lower() in ("1", "true", "yes")

        def deepl_only(tx: list[str]) -> list[str]:
            return deepl_translate_batch(tx, api_key=dk, free_api=not use_pro)

        return ("DeepL", deepl_only)

    if hybrid and llm_configured() and mk:
        region = os.getenv("AZURE_TRANSLATOR_REGION", "").strip() or None

        def azure_fn(tx: list[str]) -> list[str]:
            return azure_translate_batch(tx, api_key=mk, region=region)

        def hybrid_azure(tx: list[str]) -> list[str]:
            return hybrid_mt_deepseek_batch(tx, mt_batch_fn=azure_fn, api_key=None)

        return ("Azure+DeepSeek 混合", hybrid_azure)

    if mk:
        region = os.getenv("AZURE_TRANSLATOR_REGION", "").strip() or None

        def azure_only(tx: list[str]) -> list[str]:
            return azure_translate_batch(tx, api_key=mk, region=region)

        return ("Azure", azure_only)

    if ds:

        def deepseek_only(tx: list[str]) -> list[str]:
            return deepseek_translate_batch(tx, api_key=ds)

        return ("DeepSeek", deepseek_only)

    if llm_configured():

        def llm_fn(tx: list[str]) -> list[str]:
            return openai_translate_batch(tx, api_key=None)

        return ("大模型(OpenAI兼容)", llm_fn)

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

    if eng == "deepseek":
        ds = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not ds:
            print("错误: --engine deepseek 需要 DEEPSEEK_API_KEY", file=sys.stderr)
            sys.exit(3)

        def fn_ds(tx: list[str]) -> list[str]:
            return deepseek_translate_batch(tx, api_key=ds)

        return "DeepSeek", fn_ds

    if eng == "openai":
        if not llm_configured():
            print(
                "错误: --engine openai 需要 YOUTOBE_LLM_API_KEY、DEEPSEEK_API_KEY 或 OPENAI_API_KEY",
                file=sys.stderr,
            )
            sys.exit(3)

        def fn3(tx: list[str]) -> list[str]:
            return openai_translate_batch(tx, api_key=None)

        return "大模型(OpenAI兼容)", fn3

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


def _maybe_apply_reading_time_align(
    out: pysrt.SubRipFile,
    subs_en: list,
    texts: list[str],
    dst: Path,
    *,
    sleep_s: float,
    enabled: bool,
) -> None:
    """
    翻译完成后：用 subtitle_reading_time 扫描 over/under，多轮大模型改写直至达标或达轮次上限；
    单批返回条数不一致时自动拆半/逐条重试。
    """
    if not enabled:
        return
    from subtitle_reading_time import (
        reading_align_thresholds,
        scan_subtitle_pairs,
        summarize_scan,
    )

    n = len(subs_en)
    if n == 0 or len(out) != n:
        return
    if not llm_configured():
        print(
            "朗读时长对齐: 未配置大模型 Key，跳过自动改写（仍可在配音阶段做时长压缩）。",
            file=sys.stderr,
        )
        return

    max_passes = int(os.getenv("YOUTOBE_TRANSLATE_READING_ALIGN_PASSES", "5").strip() or "5")
    max_passes = max(1, min(max_passes, 8))
    chunk_sz = int(os.getenv("YOUTOBE_TRANSLATE_READING_ALIGN_CHUNK", "12").strip() or "12")
    chunk_sz = max(4, min(chunk_sz, 18))

    def _apply_batch_indices(batch_i: list[int], rows: list[dict], cps: float) -> int:
        """返回本批成功写回条数。"""
        if not batch_i:
            return 0

        def _one_items(indices: list[int]) -> list[tuple[str, str, float, str]]:
            items2: list[tuple[str, str, float, str]] = []
            for i in indices:
                en = (texts[i] if i < len(texts) else "") or subs_en[i].text
                zh0 = out[i].text
                slot = float(rows[i]["slot_sec"])
                issue = str(rows[i]["issue"])
                items2.append((en, zh0, slot, issue))
            return items2

        def _call_align(indices: list[int]) -> list[str] | None:
            it = _one_items(indices)
            try:
                zh_new = zh_reading_time_align_batch(
                    it,
                    api_key=None,
                    chars_per_sec_budget=cps,
                    squeeze_round=pass_num,
                )
            except Exception as e:
                print(f"朗读时长对齐: API 失败 ({e})，尝试拆批…", file=sys.stderr)
                return None
            if len(zh_new) != len(indices):
                return None
            return zh_new

        changed = 0
        zh_new = _call_align(batch_i)
        if zh_new is not None:
            for k, i in enumerate(batch_i):
                out[i].text = zh_new[k]
                changed += 1
            return changed

        if len(batch_i) == 1:
            return 0

        mid = max(1, len(batch_i) // 2)
        a = batch_i[:mid]
        b = batch_i[mid:]
        changed += _apply_batch_indices(a, rows, cps)
        changed += _apply_batch_indices(b, rows, cps)
        if changed == 0 and len(batch_i) <= 4:
            for i in batch_i:
                one = _call_align([i])
                if one is not None:
                    out[i].text = one[0]
                    changed += 1
                time.sleep(max(sleep_s, 0.12))
        return changed

    total_changed = 0
    for pass_num in range(max_passes):
        zh_lines = [out[i].text for i in range(n)]
        cps, over_r, under_r, min_u, bias = reading_align_thresholds()
        rows = scan_subtitle_pairs(
            subs_en,
            zh_lines,
            cps=cps,
            over_ratio=over_r,
            under_ratio=under_r,
            min_slot_for_under=min_u,
            units_tts_bias=bias,
        )
        sk, ok_c, ov, un = summarize_scan(rows)
        need_idx = [
            i
            for i, r in enumerate(rows)
            if r["issue"] in ("over", "under") and (texts[i] if i < len(texts) else "").strip()
        ]
        if pass_num == 0:
            print(
                f"朗读时长对齐: 扫描 {n} 条（skip {sk} / ok {ok_c} / over {ov} / under {un}），"
                f"待改写 {len(need_idx)} 条（CPS≈{cps:.2f}，最多 {max_passes} 轮）。",
                file=sys.stderr,
            )
        else:
            print(
                f"朗读时长对齐: 第 {pass_num + 1} 轮扫描 — skip {sk} / ok {ok_c} / over {ov} / under {un}，"
                f"待改写 {len(need_idx)} 条。",
                file=sys.stderr,
            )
        if not need_idx:
            break

        late_chunk = int(os.getenv("YOUTOBE_TRANSLATE_READING_ALIGN_CHUNK_LATE", "0").strip() or "0")
        eff_chunk = (
            max(4, min(late_chunk, 18))
            if late_chunk >= 4 and pass_num >= 2
            else (max(4, chunk_sz // 2) if pass_num >= 2 else chunk_sz)
        )

        pass_changed = 0
        for a in range(0, len(need_idx), eff_chunk):
            batch_i = need_idx[a : a + eff_chunk]
            pass_changed += _apply_batch_indices(batch_i, rows, cps)
            dst.parent.mkdir(parents=True, exist_ok=True)
            out.save(str(dst), encoding="utf-8")
            print(
                f"  朗读对齐已保存: {min(a + eff_chunk, len(need_idx))}/{len(need_idx)} 条本轮待处理",
                file=sys.stderr,
            )
            time.sleep(max(sleep_s, 0.18))
        total_changed += pass_changed
        if pass_changed == 0:
            print(
                "朗读时长对齐: 本轮未能改写任何条目，停止多轮以免空转。",
                file=sys.stderr,
            )
            break

    print(f"朗读时长对齐: 完成，累计改写 {total_changed} 条。", file=sys.stderr)


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
    reading_time_align: bool | None = None,
) -> None:
    if reading_time_align is None:
        reading_time_align = os.getenv(
            "YOUTOBE_TRANSLATE_READING_ALIGN", "1"
        ).strip().lower() not in ("0", "false", "no", "off", "")
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

    use_fluent_llm = bool(speech_smooth and llm_configured())
    if speech_smooth and not use_fluent_llm:
        texts = _local_smooth_lines(texts)
        print(
            "口语顺化: 已做本地轻量去填料（配置 YOUTOBE_LLM_API_KEY、DEEPSEEK_API_KEY 或 OPENAI_API_KEY 后可用大模型语境顺化+中译）",
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
                out_done = pysrt.open(str(dst))
                _maybe_apply_reading_time_align(
                    out_done,
                    subs_en,
                    texts,
                    dst,
                    sleep_s=sleep_s,
                    enabled=reading_time_align,
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

    if use_fluent_llm:

        def batch_fn(tx: list[str]) -> list[str]:
            return openai_fluent_zh_batch(tx, api_key=None)

        batch_name = "大模型（语境顺化→中文）"
        use_batch = True
        print(
            "口语顺化: 使用大模型在批次内合并语境并输出中文（每批条数/字符已适度收紧）",
            file=sys.stderr,
        )
    else:
        batch_name, batch_fn = _resolve_batch_translator(eng)
        use_batch = batch_fn is not None

    eff_lines = min(batch_lines, 22) if use_fluent_llm else batch_lines
    eff_chars = min(batch_chars, 5500) if use_fluent_llm else batch_chars

    if use_batch:
        print(
            f"翻译后端: {batch_name}（批量，约每批 {eff_lines} 条）",
            file=sys.stderr,
        )
    elif eng == "smart":
        print(
            "翻译后端: Google / MyMemory（未检测到 DeepSeek / DeepL / Azure / 大模型(YOUTOBE_LLM/DeepSeek/OpenAI) / 阿里云 / 腾讯云 Key）",
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

    _maybe_apply_reading_time_align(
        out,
        subs_en,
        texts,
        dst,
        sleep_s=sleep_s,
        enabled=reading_time_align,
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

    ap = argparse.ArgumentParser(description="SRT 英译中（批量 API + 续译）")
    ap.add_argument("src", type=Path, help="英文 SRT")
    ap.add_argument("dst", type=Path, nargs="?", help="输出中文 SRT")
    ap.add_argument("--sleep", type=float, default=0.12, help="每批之间的间隔（秒）")
    ap.add_argument(
        "--engine",
        default="smart",
        help=(
            "smart: 见 env（大模型+DeepL/Azure 混合 > 单 DeepL/Azure/DeepSeek > 大模型(OpenAI兼容) > 云厂商 > Google）；"
            "deepl|azure|deepseek|openai|aliyun|tencent|google|mymemory 强制单一后端"
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
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "口语顺化（默认开；--no-speech-smooth 关闭）：已配置 YOUTOBE_LLM_API_KEY / DEEPSEEK_API_KEY / "
            "OPENAI_API_KEY 之一时，用大模型在批次内合并语境并输出中文；否则先对英文做本地轻量去填料再按 --engine 翻译"
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
    ap.add_argument(
        "--reading-time-align",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "翻译后按英文条时长与中文预估朗读时间对齐（需大模型 Key；"
            "关：--no-reading-time-align；也可用环境变量 YOUTOBE_TRANSLATE_READING_ALIGN=0）"
        ),
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
        reading_time_align=args.reading_time_align,
    )
    print(str(dst.resolve()))


if __name__ == "__main__":
    main()
