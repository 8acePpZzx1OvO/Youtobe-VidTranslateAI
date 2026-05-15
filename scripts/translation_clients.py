"""
翻译 HTTP/SDK 封装：DeepL、Azure、OpenAI、阿里云机器翻译、腾讯云 TMT 等。
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests


def deepl_translate_batch(
    texts: list[str],
    *,
    api_key: str,
    free_api: bool = True,
    source_lang: str = "EN",
    target_lang: str = "ZH",
    timeout: float = 60.0,
) -> list[str]:
    """DeepL v2：同一请求内多条 text=。"""
    if not texts:
        return []
    base = (
        "https://api-free.deepl.com/v2/translate"
        if free_api
        else "https://api.deepl.com/v2/translate"
    )
    data: list[tuple[str, str]] = [
        ("auth_key", api_key),
        ("source_lang", source_lang),
        ("target_lang", target_lang),
    ]
    for t in texts:
        data.append(("text", t))
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            r = requests.post(base, data=data, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2.0 + attempt * 3.0)
                continue
            r.raise_for_status()
            js = r.json()
            out = [x["text"] for x in js.get("translations", [])]
            if len(out) != len(texts):
                raise RuntimeError(
                    f"DeepL 返回条数 {len(out)} 与请求 {len(texts)} 不一致"
                )
            return out
        except Exception as e:
            last_err = e
            time.sleep(1.0 + attempt * 1.5)
    raise RuntimeError(f"DeepL 批量翻译失败: {last_err}") from last_err


def azure_translate_batch(
    texts: list[str],
    *,
    api_key: str,
    region: str | None = None,
    source: str = "en",
    target: str = "zh-Hans",
    timeout: float = 60.0,
) -> list[str]:
    """Azure Translator v3：JSON 数组一次翻译多条。"""
    if not texts:
        return []
    url = (
        "https://api.cognitive.microsofttranslator.com/translate"
        f"?api-version=3.0&from={source}&to={target}"
    )
    headers: dict[str, str] = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Content-Type": "application/json",
    }
    if region:
        headers["Ocp-Apim-Subscription-Region"] = region
    body: list[dict[str, str]] = [{"text": t} for t in texts]
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2.0 + attempt * 3.0)
                continue
            if not r.ok:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
            data: Any = r.json()
            if not isinstance(data, list) or len(data) != len(texts):
                raise RuntimeError(f"Azure 响应异常: {str(data)[:300]}")
            return [item["translations"][0]["text"] for item in data]
        except Exception as e:
            last_err = e
            time.sleep(1.0 + attempt * 1.5)
    raise RuntimeError(f"Azure 批量翻译失败: {last_err}") from last_err


def openai_translate_batch(
    texts: list[str],
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> list[str]:
    """OpenAI Chat Completions：一次请求翻译多条，要求返回 JSON 数组。"""
    if not texts:
        return []
    import json

    m = model or os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-4o-mini")
    url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip(
        "/"
    ) + "/chat/completions"
    sys_prompt = (
        "You translate subtitle lines from English to Simplified Chinese. "
        "Preserve order and count. Output ONLY valid JSON: "
        '{"translations":["...","..."]} one string per input line, same length as input.'
    )
    user_payload = json.dumps({"lines": texts}, ensure_ascii=False)
    body = {
        "model": m,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_payload},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    r.raise_for_status()
    js = r.json()
    content = js["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        import re as _re

        m = _re.search(r"\{[\s\S]*\"translations\"[\s\S]*\}", content)
        if not m:
            raise RuntimeError(f"无法解析 OpenAI 返回: {content[:400]}") from e
        parsed = json.loads(m.group(0))
    arr = parsed.get("translations")
    if not isinstance(arr, list) or len(arr) != len(texts):
        raise RuntimeError("OpenAI 返回 JSON 条数与输入不一致")
    return [str(x).strip() for x in arr]


def openai_fluent_zh_batch(
    texts: list[str],
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 180.0,
) -> list[str]:
    """
    将连续口语字幕视为同一段对话：去口吃/无意义重复与英文填料（uh, um, like 等），
    再译为通顺简体中文；输出条数与顺序必须与输入一致（每行对应原时间轴一条 cue）。
    """
    if not texts:
        return []
    import json
    import re as _re

    m = model or os.getenv(
        "OPENAI_FLUENT_MODEL", os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-4o-mini")
    )
    url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip(
        "/"
    ) + "/chat/completions"
    sys_prompt = (
        "You process consecutive English SUBTITLE cues from spoken video. "
        "The input JSON field \"lines\" is chronological; each element is ONE cue "
        "(its own on-screen time window). "
        "Speakers may stutter, repeat words, or use verbal fillers (uh, um, er, ah, "
        "like, you know, I mean, sort of). Use context across cues to infer intent. "
        "Task: output natural Simplified Chinese for each cue, as if the speech were "
        "fluent—do NOT mirror fillers or meaningless repetition in Chinese "
        "(avoid translating pure fillers to 呃、嗯、就是 unless they carry attitude). "
        "Prefer concise wording that sounds natural when read aloud in one breath (口语节奏). "
        "Keep exactly one output string per input line (same array length and order). "
        "If a cue is only noise after smoothing, output \"\" or a minimal connective. "
        "Do not merge two cues into one long paragraph for one index; each translation "
        "should fit roughly one subtitle line (can be shorter than a literal translation). "
        "Output ONLY valid JSON: {\"translations\":[\"...\",...]} with len(translations)==len(lines)."
    )
    user_payload = json.dumps({"lines": texts}, ensure_ascii=False)
    body = {
        "model": m,
        "temperature": 0.25,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_payload},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
            r.raise_for_status()
            js = r.json()
            content = js["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"translations\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析 OpenAI 返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("translations")
            if not isinstance(arr, list) or len(arr) != len(texts):
                raise RuntimeError(
                    f"OpenAI 返回条数 {len(arr) if isinstance(arr, list) else '?'} "
                    f"与输入 {len(texts)} 不一致"
                )
            return [str(x).strip() for x in arr]
        except Exception as e:
            last_err = e
            time.sleep(1.2 + attempt * 2.0)
    raise RuntimeError(f"OpenAI 口语顺化翻译失败: {last_err}") from last_err


def openai_colloquial_zh_batch(
    texts: list[str],
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> list[str]:
    """
    将已写好的中文配音稿（可能含翻译腔）改为适合口播的简体口语，条数与顺序不变。
    """
    if not texts:
        return []
    import json
    import re as _re

    m = model or os.getenv(
        "OPENAI_COLLOQUIAL_MODEL",
        os.getenv(
            "OPENAI_FLUENT_MODEL",
            os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-4o-mini"),
        ),
    )
    url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip(
        "/"
    ) + "/chat/completions"
    sys_prompt = (
        "You rewrite Chinese lines for VOICE DUBBING (spoken narration). "
        "Keep meaning and proper nouns. Make each line colloquial 口语化: short clauses, easy to read aloud, "
        "no translationese or overly formal written tone. "
        "Optimize for neural TTS: prefer commas over semicolons for natural micro-pauses; "
        "avoid parentheses, brackets, and stage directions; spell out Arabic digits in Chinese words when few (e.g. 三 instead of 3) unless years/IDs; "
        "remove English except unavoidable proper nouns; avoid rare archaic characters. "
        "Do NOT add host greetings, meta comments, quotes, or stage directions. "
        'Input JSON has key \"lines\" (string array). Output ONLY JSON: {\"lines\":[\"...\",...]} '
        "with the SAME length and order as input."
    )
    user_payload = json.dumps({"lines": texts}, ensure_ascii=False)
    body = {
        "model": m,
        "temperature": 0.35,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_payload},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
            r.raise_for_status()
            js = r.json()
            content = js["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"lines\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析 OpenAI 返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("lines")
            if not isinstance(arr, list) or len(arr) != len(texts):
                raise RuntimeError(
                    f"OpenAI 口语化返回条数 {len(arr) if isinstance(arr, list) else '?'} "
                    f"与输入 {len(texts)} 不一致"
                )
            return [str(x).strip() for x in arr]
        except Exception as e:
            last_err = e
            time.sleep(1.0 + attempt * 1.8)
    raise RuntimeError(f"OpenAI 口语化失败: {last_err}") from last_err


def openai_tts_polish_batch(
    texts: list[str],
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> list[str]:
    """
    二次润色：仅针对 TTS 朗读（不改条数），去括号说明、控制长度与标点，利于 Edge/火山断句。
    """
    if not texts:
        return []
    import json
    import re as _re

    m = model or os.getenv(
        "OPENAI_TTS_POLISH_MODEL",
        os.getenv("OPENAI_COLLOQUIAL_MODEL", os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-4o-mini")),
    )
    url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip(
        "/"
    ) + "/chat/completions"
    sys_prompt = (
        "Polish Chinese strings ONLY for neural text-to-speech (same array length and order). "
        "Output ONLY JSON: {\"lines\":[\"...\",...]} . "
        "Rules: remove content in parentheses/brackets that a narrator would skip; "
        "replace ellipsis … with comma or period; no newlines inside strings; "
        "if a line is over ~85 Chinese characters, shorten by rephrasing without losing core meaning; "
        "remove English except unavoidable proper nouns; use ，、 for natural pauses; "
        "no ALL-CAPS emphasis; no markdown."
    )
    user_payload = json.dumps({"lines": texts}, ensure_ascii=False)
    body = {
        "model": m,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_payload},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
            r.raise_for_status()
            js = r.json()
            content = js["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"lines\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析 OpenAI 返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("lines")
            if not isinstance(arr, list) or len(arr) != len(texts):
                raise RuntimeError(
                    f"OpenAI TTS 润色返回条数 {len(arr) if isinstance(arr, list) else '?'} "
                    f"与输入 {len(texts)} 不一致"
                )
            return [str(x).strip() for x in arr]
        except Exception as e:
            last_err = e
            time.sleep(1.0 + attempt * 1.8)
    raise RuntimeError(f"OpenAI TTS 润色失败: {last_err}") from last_err


def openai_dub_duration_fit_batch(
    items: list[tuple[str, str, float]],
    *,
    api_key: str,
    chars_per_sec_budget: float = 3.45,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> list[str]:
    """
    按「英文原句在视频里的时长 slot_sec」压缩/改写中文口播稿，使 TTS 大致能落在该时段内，
    停顿与断句尽量跟随英文句子的意群（不改变核心事实，允许省略赘述、合并从句为短口语）。
    items: (english_line, chinese_line, slot_seconds)，条数与返回 lines 一致。
    """
    if not items:
        return []
    import json
    import math
    import re as _re

    m = model or os.getenv(
        "OPENAI_DUB_DURATION_FIT_MODEL",
        os.getenv("OPENAI_COLLOQUIAL_MODEL", os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-4o-mini")),
    )
    url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip(
        "/"
    ) + "/chat/completions"
    sys_prompt = (
        "You compress Simplified Chinese dubbing lines for neural TTS. "
        "Same count and order as input items. Output ONLY JSON: {\"lines\":[\"...\",...]} . "
        "Each input item has: English subtitle line (timing reference), Chinese translation, "
        "slot_seconds = how long that English line lasts in the film. "
        "Rewrite Chinese so a natural Chinese voice can finish within slot_seconds "
        f"(budget roughly {chars_per_sec_budget:.2f} Chinese characters per second of slot; stay under budget). "
        "Preserve core meaning vs the Chinese line and facts implied by English; drop redundancy, "
        "avoid long subordinate clauses; use oral concise Chinese. "
        "Place commas ，、 where short pauses help; avoid semicolons; no newlines in strings; "
        "no parentheses/stage directions; no English except unavoidable proper nouns; no markdown. "
        "If Chinese is already short enough, return it lightly polished only."
    )
    payload_items = [
        {"en": en, "zh": zh, "slot_sec": round(sec, 3), "max_chars_hint": max(4, int(math.ceil(sec * chars_per_sec_budget * 1.05)))}
        for en, zh, sec in items
    ]
    user_payload = json.dumps({"items": payload_items}, ensure_ascii=False)
    body = {
        "model": m,
        "temperature": 0.25,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_payload},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
            r.raise_for_status()
            js = r.json()
            content = js["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"lines\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析 OpenAI 返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("lines")
            if not isinstance(arr, list) or len(arr) != len(items):
                raise RuntimeError(
                    f"OpenAI 时长适配返回条数 {len(arr) if isinstance(arr, list) else '?'} "
                    f"与输入 {len(items)} 不一致"
                )
            return [str(x).strip() for x in arr]
        except Exception as e:
            last_err = e
            time.sleep(1.0 + attempt * 1.8)
    raise RuntimeError(f"OpenAI 配音时长适配失败: {last_err}") from last_err


def aliyun_translate_parallel(
    texts: list[str],
    *,
    access_key_id: str,
    access_key_secret: str,
    region: str = "cn-hangzhou",
    source_lang: str = "en",
    target_lang: str = "zh",
    max_workers: int | None = None,
) -> list[str]:
    """
    阿里云机器翻译「通用版」TranslateGeneral：单条上限约 5000 字符。
    多条通过线程池并发（默认 6，可用环境变量 ALIYUN_MT_CONCURRENCY 调整）。
    """
    if not texts:
        return []
    try:
        from aliyunsdkcore.client import AcsClient
        from aliyunsdkalimt.request.v20181012.TranslateGeneralRequest import (
            TranslateGeneralRequest,
        )
    except ImportError as e:
        raise RuntimeError(
            "请安装: pip install aliyun-python-sdk-core aliyun-python-sdk-alimt"
        ) from e

    import json
    from concurrent.futures import ThreadPoolExecutor, as_completed

    mw = max_workers
    if mw is None:
        mw = int(os.getenv("ALIYUN_MT_CONCURRENCY", "6"))
    mw = max(1, min(mw, 20))

    def _one(idx: int, raw: str) -> tuple[int, str]:
        st = raw if raw.strip() else " "
        if len(st) > 4800:
            st = st[:4800]
        client = AcsClient(access_key_id, access_key_secret, region)
        req = TranslateGeneralRequest()
        req.set_FormatType("text")
        req.set_Scene("general")
        req.set_SourceLanguage(source_lang)
        req.set_TargetLanguage(target_lang)
        req.set_SourceText(st)
        body = client.do_action_with_exception(req)
        data = json.loads(body.decode("utf-8"))
        code = data.get("Code")
        if code not in (200, "200"):
            raise RuntimeError(data.get("Message") or str(data)[:400])
        translated = data.get("Data", {}).get("Translated", "")
        return idx, str(translated).strip()

    out = [""] * len(texts)
    with ThreadPoolExecutor(max_workers=mw) as pool:
        futures = [pool.submit(_one, i, texts[i]) for i in range(len(texts))]
        for fut in as_completed(futures):
            i, zh = fut.result()
            out[i] = zh
    return out


def tencent_translate_parallel(
    texts: list[str],
    *,
    secret_id: str,
    secret_key: str,
    region: str = "ap-beijing",
    source: str = "en",
    target: str = "zh",
    project_id: int = 0,
    max_workers: int | None = None,
) -> list[str]:
    """
    腾讯云 TMT TextTranslate：单条请求；多条用线程池并发（默认 3，防 QPS；TENCENT_TMT_CONCURRENCY）。
    """
    if not texts:
        return []
    try:
        from tencentcloud.common import credential
        from tencentcloud.tmt.v20180321 import models, tmt_client
    except ImportError as e:
        raise RuntimeError("请安装: pip install tencentcloud-sdk-python") from e

    from concurrent.futures import ThreadPoolExecutor, as_completed

    mw = max_workers
    if mw is None:
        mw = int(os.getenv("TENCENT_TMT_CONCURRENCY", "3"))
    mw = max(1, min(mw, 8))

    def _one(idx: int, raw: str) -> tuple[int, str]:
        st = raw if raw.strip() else " "
        if len(st) > 1800:
            st = st[:1800]
        cred = credential.Credential(secret_id, secret_key)
        client = tmt_client.TmtClient(cred, region)
        req = models.TextTranslateRequest()
        req.SourceText = st
        req.Source = source
        req.Target = target
        req.ProjectId = project_id
        resp = client.TextTranslate(req)
        return idx, (resp.TargetText or "").strip()

    out = [""] * len(texts)
    with ThreadPoolExecutor(max_workers=mw) as pool:
        futures = [pool.submit(_one, i, texts[i]) for i in range(len(texts))]
        for fut in as_completed(futures):
            i, zh = fut.result()
            out[i] = zh
    return out


def aliyun_translate_batch(
    texts: list[str],
    *,
    access_key_id: str,
    access_key_secret: str,
    region: str = "cn-hangzhou",
) -> list[str]:
    """与 translate_srt 批量接口一致：内部并发多条。"""
    return aliyun_translate_parallel(
        texts,
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        region=region,
    )


def tencent_translate_batch(
    texts: list[str],
    *,
    secret_id: str,
    secret_key: str,
    region: str = "ap-beijing",
    project_id: int = 0,
) -> list[str]:
    return tencent_translate_parallel(
        texts,
        secret_id=secret_id,
        secret_key=secret_key,
        region=region,
        project_id=project_id,
    )
