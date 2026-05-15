"""
【模块】translation_clients.py — 多后端翻译、大模型批处理与配音辅助（口语化后缀、dub 情绪 delivery 推断等）。
【调用方】translate_srt.py、dub_zh.py 等 import；不单独作 CLI。

翻译 HTTP/SDK 封装：DeepL、Azure、兼容 OpenAI Chat 的大模型（YOUTOBE_LLM / DeepSeek / 旧 OpenAI）、阿里云机器翻译、腾讯云 TMT 等。
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import requests

# 翻译/顺化共用：强化口语化（避免翻译腔），供各 LLM 系统提示拼接。
_ORAL_ZH_SUFFIX = (
    " Strong preference for mainland colloquial zh-CN: short clauses, natural spoken word order; "
    "avoid translationese (e.g. 关于/进行/被认为/所…的/之道) and English clause nesting copied literally. "
    "Render surprise, teasing, urgency, irony, and emphasis the way Chinese YouTubers talk on camera, "
    "not like a textbook or newsreader—still subtitle-brief."
)


def _align_llm_json_lines(
    arr: Any,
    originals: list[str],
    *,
    tag: str = "lines",
) -> list[str]:
    """
    将大模型 JSON 里的 lines 对齐为与 originals 等长。
    少行：用原稿补齐；多行：优先反复合并「相邻字符和最短」的一对（常见为模型多拆出一条）。
    极多出行（>20）则截断并回填原稿，避免异常响应拖死合并循环。
    """
    n = len(originals)
    if n == 0:
        return []
    if not isinstance(arr, list):
        raise TypeError(f"{tag}: lines 须为 JSON 数组，当前为 {type(arr).__name__}")
    parts = [str(x).strip() for x in arr]
    if len(parts) == n:
        return [parts[i] or originals[i] for i in range(n)]
    if len(parts) < n:
        print(
            f"提示: {tag} 返回 {len(parts)} 条，输入 {n} 条，已用原稿补齐缺行。",
            file=sys.stderr,
        )
        return [
            (parts[i] if i < len(parts) and parts[i] else originals[i])
            for i in range(n)
        ]
    excess = len(parts) - n
    if excess > 20:
        print(
            f"提示: {tag} 多返回 {excess} 条（>20），仅保留前 {n} 条并对空行回填原稿。",
            file=sys.stderr,
        )
        return [
            (parts[i] if i < len(parts) and parts[i] else originals[i])
            for i in range(n)
        ]
    print(
        f"提示: {tag} 多返回 {excess} 条（{len(parts)}→{n}），已按相邻最短行合并对齐。",
        file=sys.stderr,
    )
    while len(parts) > n and len(parts) >= 2:
        best_i = 0
        best_score = len(parts[0]) + len(parts[1])
        for i in range(len(parts) - 1):
            s = len(parts[i]) + len(parts[i + 1])
            if s < best_score:
                best_score = s
                best_i = i
        a, b = parts[best_i], parts[best_i + 1]
        merged = (a + ("，" if a and b else "") + b).strip()
        parts[best_i : best_i + 2] = [merged]
    if len(parts) > n:
        parts = parts[:n]
    while len(parts) < n:
        parts.append("")
    return [parts[i] if parts[i] else originals[i] for i in range(n)]


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


# --- 统一大模型（OpenAI 兼容 /v1/chat/completions）：国内网关 YOUTOBE_LLM > DeepSeek > 旧 OpenAI ---


def _chat_completions_endpoint(base_root: str) -> str:
    b = base_root.rstrip("/")
    if b.endswith("/v1"):
        return f"{b}/chat/completions"
    return f"{b}/v1/chat/completions"


def llm_configured() -> bool:
    return bool(
        os.getenv("YOUTOBE_LLM_API_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )


def _pick_first_model(env_names: tuple[str, ...], *, fallback: str) -> str:
    for name in env_names:
        v = os.getenv(name, "").strip()
        if v:
            return v
    return fallback


def resolve_llm_chat(model_env_vars: tuple[str, ...]) -> tuple[str, str, str, str]:
    """
    解析 OpenAI-兼容 Chat 接口。
    返回 (api_key, chat_completions_url, model, provider_tag)。
    优先级：YOUTOBE_LLM（国内兼容，如硅基流动/通义兼容模式等）> DeepSeek > OpenAI（仅兼容旧 .env）。
    """
    key = os.getenv("YOUTOBE_LLM_API_KEY", "").strip()
    if key:
        root = (
            os.getenv("YOUTOBE_LLM_BASE_URL", "").strip()
            or "https://api.siliconflow.cn/v1"
        )
        fb = os.getenv("YOUTOBE_LLM_MODEL", "").strip() or "Qwen/Qwen2.5-7B-Instruct"
        m = _pick_first_model(model_env_vars + ("YOUTOBE_LLM_MODEL",), fallback=fb)
        return key, _chat_completions_endpoint(root), m, "YOUTOBE_LLM"
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if key:
        root = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
        m = _pick_first_model(
            model_env_vars + ("DEEPSEEK_TRANSLATION_MODEL",),
            fallback="deepseek-chat",
        )
        return key, _chat_completions_endpoint(root), m, "DeepSeek"
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if key:
        root = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
        m = _pick_first_model(
            model_env_vars + ("OPENAI_TRANSLATION_MODEL",),
            fallback="gpt-4o-mini",
        )
        return key, _chat_completions_endpoint(root), m, "OpenAI"
    raise RuntimeError(
        "未配置可用的大模型：请设置 YOUTOBE_LLM_API_KEY（国内 OpenAI 兼容接口，推荐）"
        "或 DEEPSEEK_API_KEY；仅兼容旧环境时可设 OPENAI_API_KEY"
    )


def _llm_post_chat_content(
    *,
    chat_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    timeout: float,
) -> str:
    body = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    r = requests.post(chat_url, headers=headers, json=body, timeout=timeout)
    r.raise_for_status()
    js = r.json()
    return str(js["choices"][0]["message"]["content"]).strip()


def _resolve_llm_or_explicit(
    *,
    model_env_vars: tuple[str, ...],
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    fallback_openai_model: str,
) -> tuple[str, str, str]:
    """
    返回 (api_key, chat_url, model)。
    显式传入 api_key + base_url 时走直连（兼容旧调用）；否则按 YOUTOBE_LLM > DeepSeek > OpenAI。
    """
    ak = (api_key or "").strip()
    if ak and (base_url or "").strip():
        root = str(base_url).strip().rstrip("/")
        m = (model or "").strip() or _pick_first_model(
            model_env_vars, fallback=fallback_openai_model
        )
        return ak, _chat_completions_endpoint(root), m
    if ak and os.getenv("OPENAI_BASE_URL", "").strip():
        root = os.getenv("OPENAI_BASE_URL", "").strip().rstrip("/")
        m = (model or "").strip() or _pick_first_model(
            model_env_vars + ("OPENAI_TRANSLATION_MODEL",),
            fallback=fallback_openai_model,
        )
        return ak, _chat_completions_endpoint(root), m
    k2, url, m2, _ = resolve_llm_chat(model_env_vars)
    if model:
        m2 = model.strip()
    return k2, url, m2


def openai_translate_batch(
    texts: list[str],
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> list[str]:
    """
    OpenAI 兼容 Chat：一次请求翻译多条（实际路由由 .env 决定，见 resolve_llm_chat）。
    保留函数名以兼容现有 import；api_key 可省略，将自动读取 YOUTOBE_LLM / DeepSeek / OpenAI。
    """
    if not texts:
        return []
    import json
    import re as _re

    ak, url, m = _resolve_llm_or_explicit(
        model_env_vars=(
            "YOUTOBE_LLM_TRANSLATION_MODEL",
            "OPENAI_TRANSLATION_MODEL",
            "DEEPSEEK_TRANSLATION_MODEL",
        ),
        api_key=api_key,
        base_url=base_url,
        model=model,
        fallback_openai_model="gpt-4o-mini",
    )
    sys_prompt = (
        "You translate English SUBTITLE cues to Simplified Chinese for YouTube narration. "
        "Preserve order and count exactly. Output ONLY valid JSON: "
        '{"translations":["...","..."]} with len(translations)==len(lines). '
        "Style: natural mainland Chinese as people actually speak—avoid literal calques, "
        "stiff word-by-word order, and encyclopedia tone; keep conversational register when "
        "the English sounds spoken. Keep each line roughly subtitle-length (not a long paragraph). "
        + _ORAL_ZH_SUFFIX
        + " Retain names/brands; do not add commentary."
    )
    user_payload = json.dumps({"lines": texts}, ensure_ascii=False)
    t_llm = float(os.getenv("YOUTOBE_LLM_TRANSLATION_TEMP", "").strip() or "0.32")
    t_llm = max(0.12, min(t_llm, 0.72))
    content = _llm_post_chat_content(
        chat_url=url,
        api_key=ak,
        model=m,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_payload},
        ],
        temperature=t_llm,
        timeout=timeout,
    )
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        mm = _re.search(r"\{[\s\S]*\"translations\"[\s\S]*\}", content)
        if not mm:
            raise RuntimeError(f"无法解析大模型返回: {content[:400]}") from e
        parsed = json.loads(mm.group(0))
    arr = parsed.get("translations")
    if not isinstance(arr, list) or len(arr) != len(texts):
        raise RuntimeError("大模型返回 JSON 条数与输入不一致")
    return [str(x).strip() for x in arr]


# --- DeepSeek（OpenAI 兼容 /v1/chat/completions）---


def _deepseek_chat_url(base_url: str | None) -> str:
    root = (base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip(
        "/"
    )
    if root.endswith("/v1"):
        return f"{root}/chat/completions"
    return f"{root}/v1/chat/completions"


def _deepseek_default_model() -> str:
    return os.getenv("DEEPSEEK_TRANSLATION_MODEL", "deepseek-chat").strip() or "deepseek-chat"


def _deepseek_post_chat(
    *,
    api_key: str,
    base_url: str | None,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    timeout: float,
) -> str:
    url = _deepseek_chat_url(base_url)
    return _llm_post_chat_content(
        chat_url=url,
        api_key=api_key,
        model=model,
        messages=messages,
        temperature=temperature,
        timeout=timeout,
    )


def deepseek_translate_batch(
    texts: list[str],
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> list[str]:
    """DeepSeek Chat：一次请求英译中多条，JSON 约定与 openai_translate_batch 相同。"""
    if not texts:
        return []
    import json
    import re as _re

    m = model or _deepseek_default_model()
    sys_prompt = (
        "You translate English SUBTITLE cues to Simplified Chinese for YouTube narration. "
        "Preserve order and count exactly. Output ONLY valid JSON: "
        '{"translations":["...","..."]} with len(translations)==len(lines). '
        "Style: natural mainland Chinese as people actually speak—avoid literal calques, "
        "stiff word-by-word order, and encyclopedia tone; keep conversational register when "
        "the English sounds spoken. Keep each line roughly subtitle-length (not a long paragraph). "
        + _ORAL_ZH_SUFFIX
        + " Retain names/brands; do not add commentary."
    )
    user_payload = json.dumps({"lines": texts}, ensure_ascii=False)
    t_temp = float(os.getenv("DEEPSEEK_TRANSLATION_TEMP", "").strip() or "0.36")
    t_temp = max(0.12, min(t_temp, 0.72))
    content = _deepseek_post_chat(
        api_key=api_key,
        base_url=base_url,
        model=m,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_payload},
        ],
        temperature=t_temp,
        timeout=timeout,
    )
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        mm = _re.search(r"\{[\s\S]*\"translations\"[\s\S]*\}", content)
        if not mm:
            raise RuntimeError(f"无法解析 DeepSeek 返回: {content[:400]}") from e
        parsed = json.loads(mm.group(0))
    arr = parsed.get("translations")
    if not isinstance(arr, list) or len(arr) != len(texts):
        raise RuntimeError("DeepSeek 返回 JSON 条数与输入不一致")
    return [str(x).strip() for x in arr]


def deepseek_refine_mt_batch(
    en_lines: list[str],
    zh_mt_lines: list[str],
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 180.0,
) -> list[str]:
    """
    第二遍：在 DeepL/Azure 机翻结果上，结合英文原句用大模型润色为自然口语字幕；
    失败时退回机翻。路由：YOUTOBE_LLM > DeepSeek > OpenAI（与 resolve_llm_chat 一致）。
    """
    if not en_lines:
        return []
    if len(en_lines) != len(zh_mt_lines):
        raise RuntimeError("英文条数与机翻中文条数不一致")
    import json
    import re as _re
    import sys

    pairs = [{"en": e or " ", "zh_mt": z or " "} for e, z in zip(en_lines, zh_mt_lines)]
    user_payload = json.dumps({"pairs": pairs}, ensure_ascii=False)
    sys_prompt = (
        "You polish Simplified Chinese SUBTITLE lines that were machine-translated from English "
        "(DeepL/Azure style). Input JSON has key \"pairs\": array of {en, zh_mt} in order. "
        "Use English to fix mistranslations, awkward calques, and overly formal diction; "
        "keep correct terminology from zh_mt when it matches the English. "
        "Natural spoken-YouTube Chinese; one string per pair, same count and order as \"pairs\". "
        + _ORAL_ZH_SUFFIX
        + ' Output ONLY valid JSON: {"lines":["...","..."]} with len(lines)==len(pairs). '
        "No commentary; no newlines inside strings."
    )
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            ak, url, m = _resolve_llm_or_explicit(
                model_env_vars=(
                    "DEEPSEEK_REFINE_MODEL",
                    "YOUTOBE_LLM_REFINE_MODEL",
                    "YOUTOBE_LLM_MODEL",
                    "OPENAI_TRANSLATION_MODEL",
                    "DEEPSEEK_TRANSLATION_MODEL",
                ),
                api_key=api_key,
                base_url=base_url,
                model=model,
                fallback_openai_model="gpt-4o-mini",
            )
            content = _llm_post_chat_content(
                chat_url=url,
                api_key=ak,
                model=m,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.28,
                timeout=timeout,
            )
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"lines\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析大模型润色返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("lines")
            if not isinstance(arr, list) or len(arr) != len(en_lines):
                raise RuntimeError(
                    f"大模型润色条数 {len(arr) if isinstance(arr, list) else '?'} "
                    f"与输入 {len(en_lines)} 不一致"
                )
            return [str(x).strip() for x in arr]
        except Exception as e:
            last_err = e
            time.sleep(1.0 + attempt * 1.8)
    print(
        f"警告: 大模型润色失败，已退回纯机翻: {last_err}",
        file=sys.stderr,
    )
    return [str(x).strip() for x in zh_mt_lines]


def hybrid_mt_deepseek_batch(
    texts: list[str],
    *,
    mt_batch_fn: Any,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> list[str]:
    """先机翻（DeepL/Azure），再大模型（YOUTOBE_LLM / DeepSeek / OpenAI）语境润色。"""
    if not texts:
        return []
    zh0 = mt_batch_fn(texts)
    if len(zh0) != len(texts):
        raise RuntimeError("机翻返回条数与输入不一致")
    return deepseek_refine_mt_batch(
        texts,
        zh0,
        api_key=api_key,
        model=model,
        base_url=base_url,
    )


def deepseek_fluent_zh_batch(
    texts: list[str],
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 180.0,
) -> list[str]:
    """
    口语顺化：与 openai_fluent_zh_batch 同任务，走 DeepSeek API。
    输出 JSON {\"translations\":[...]} 与输入等长。
    """
    if not texts:
        return []
    import json
    import re as _re

    m = model or os.getenv(
        "DEEPSEEK_FLUENT_MODEL",
        _deepseek_default_model(),
    )
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
        + _ORAL_ZH_SUFFIX
        + ' Output ONLY valid JSON: {"translations":["...",...]} with len(translations)==len(lines).'
    )
    user_payload = json.dumps({"lines": texts}, ensure_ascii=False)
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            content = _deepseek_post_chat(
                api_key=api_key,
                base_url=base_url,
                model=m,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.25,
                timeout=timeout,
            )
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"translations\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析 DeepSeek 返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("translations")
            if not isinstance(arr, list) or len(arr) != len(texts):
                raise RuntimeError(
                    f"DeepSeek 口语顺化返回条数 {len(arr) if isinstance(arr, list) else '?'} "
                    f"与输入 {len(texts)} 不一致"
                )
            return [str(x).strip() for x in arr]
        except Exception as e:
            last_err = e
            time.sleep(1.2 + attempt * 2.0)
    raise RuntimeError(f"DeepSeek 口语顺化翻译失败: {last_err}") from last_err


def openai_fluent_zh_batch(
    texts: list[str],
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 180.0,
) -> list[str]:
    """
    口语顺化：连续英文字幕 → 自然中文（OpenAI 兼容接口；实际路由见 resolve_llm_chat）。
    """
    if not texts:
        return []
    import json
    import re as _re

    ak, url, m = _resolve_llm_or_explicit(
        model_env_vars=(
            "YOUTOBE_LLM_FLUENT_MODEL",
            "DEEPSEEK_FLUENT_MODEL",
            "OPENAI_FLUENT_MODEL",
            "OPENAI_TRANSLATION_MODEL",
            "DEEPSEEK_TRANSLATION_MODEL",
        ),
        api_key=api_key,
        base_url=base_url,
        model=model,
        fallback_openai_model="gpt-4o-mini",
    )
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
        + _ORAL_ZH_SUFFIX
        + ' Output ONLY valid JSON: {"translations":["...",...]} with len(translations)==len(lines).'
    )
    user_payload = json.dumps({"lines": texts}, ensure_ascii=False)
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            content = _llm_post_chat_content(
                chat_url=url,
                api_key=ak,
                model=m,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.25,
                timeout=timeout,
            )
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"translations\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析大模型返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("translations")
            if not isinstance(arr, list) or len(arr) != len(texts):
                raise RuntimeError(
                    f"大模型返回条数 {len(arr) if isinstance(arr, list) else '?'} "
                    f"与输入 {len(texts)} 不一致"
                )
            return [str(x).strip() for x in arr]
        except Exception as e:
            last_err = e
            time.sleep(1.2 + attempt * 2.0)
    raise RuntimeError(f"口语顺化（大模型）失败: {last_err}") from last_err


def openai_colloquial_zh_batch(
    texts: list[str],
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> list[str]:
    """
    将已写好的中文配音稿（可能含翻译腔）改为适合口播的简体口语，条数与顺序不变。
    路由：YOUTOBE_LLM > DeepSeek > OpenAI（兼容旧 .env）。
    """
    if not texts:
        return []
    import json
    import re as _re

    ak, url, m = _resolve_llm_or_explicit(
        model_env_vars=(
            "YOUTOBE_LLM_COLLOQUIAL_MODEL",
            "OPENAI_COLLOQUIAL_MODEL",
            "YOUTOBE_LLM_FLUENT_MODEL",
            "OPENAI_FLUENT_MODEL",
            "OPENAI_TRANSLATION_MODEL",
            "DEEPSEEK_TRANSLATION_MODEL",
        ),
        api_key=api_key,
        base_url=base_url,
        model=model,
        fallback_openai_model="gpt-4o-mini",
    )
    sys_prompt = (
        "You rewrite Chinese lines for VOICE DUBBING (spoken narration). "
        "Keep meaning and proper nouns. Make each line colloquial 口语化: short clauses, easy to read aloud, "
        "no translationese or overly formal written tone. "
        "Optimize for neural TTS: prefer commas over semicolons for natural micro-pauses; "
        "avoid parentheses, brackets, and stage directions; spell out Arabic digits in Chinese words when few (e.g. 三 instead of 3) unless years/IDs; "
        "remove English except unavoidable proper nouns; avoid rare archaic characters. "
        + _ORAL_ZH_SUFFIX
        + " Do NOT add host greetings, meta comments, quotes, or stage directions. "
        'Input JSON has key \"lines\" (string array). Output ONLY JSON: {\"lines\":[\"...\",...]} '
        "with the SAME length and order as input."
    )
    user_payload = json.dumps({"lines": texts}, ensure_ascii=False)
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            content = _llm_post_chat_content(
                chat_url=url,
                api_key=ak,
                model=m,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.35,
                timeout=timeout,
            )
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"lines\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析大模型返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("lines")
            return _align_llm_json_lines(arr, texts, tag="口语化")
        except Exception as e:
            last_err = e
            time.sleep(1.0 + attempt * 1.8)
    raise RuntimeError(f"口语化（大模型）失败: {last_err}") from last_err


def openai_tts_polish_batch(
    texts: list[str],
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> list[str]:
    """
    二次润色：仅针对 TTS 朗读（不改条数）。路由：YOUTOBE_LLM > DeepSeek > OpenAI。
    """
    if not texts:
        return []
    import json
    import re as _re

    ak, url, m = _resolve_llm_or_explicit(
        model_env_vars=(
            "YOUTOBE_LLM_TTS_POLISH_MODEL",
            "OPENAI_TTS_POLISH_MODEL",
            "OPENAI_COLLOQUIAL_MODEL",
            "YOUTOBE_LLM_COLLOQUIAL_MODEL",
            "OPENAI_TRANSLATION_MODEL",
            "DEEPSEEK_TRANSLATION_MODEL",
        ),
        api_key=api_key,
        base_url=base_url,
        model=model,
        fallback_openai_model="gpt-4o-mini",
    )
    sys_prompt = (
        "Polish Chinese strings ONLY for neural text-to-speech (same array length and order). "
        "Output ONLY JSON: {\"lines\":[\"...\",...]} . "
        "Rules: remove content in parentheses/brackets that a narrator would skip; "
        "replace ellipsis … with comma or period; no newlines inside strings; "
        "if a line is over ~85 Chinese characters, shorten by rephrasing without losing core meaning; "
        "remove English except unavoidable proper nouns; use ，、 for natural pauses; "
        "end each line with a clear tone boundary (prefer 。！？ or at least ，) so the vocoder "
        "can release cleanly—avoid dangling half-phrases or orphaned particles; "
        "no ALL-CAPS emphasis; no markdown."
    )
    user_payload = json.dumps({"lines": texts}, ensure_ascii=False)
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            content = _llm_post_chat_content(
                chat_url=url,
                api_key=ak,
                model=m,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.2,
                timeout=timeout,
            )
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"lines\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析大模型返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("lines")
            return _align_llm_json_lines(arr, texts, tag="TTS润色")
        except Exception as e:
            last_err = e
            time.sleep(1.0 + attempt * 1.8)
    raise RuntimeError(f"TTS 润色（大模型）失败: {last_err}") from last_err


def openai_dub_duration_fit_batch(
    items: list[tuple[str, str, float]],
    *,
    api_key: str | None = None,
    chars_per_sec_budget: float = 3.45,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> list[str]:
    """
    按「英文原句在视频里的时长 slot_sec」压缩/改写中文口播稿。路由：YOUTOBE_LLM > DeepSeek > OpenAI。
    items: (english_line, chinese_line, slot_seconds)，条数与返回 lines 一致。
    """
    if not items:
        return []
    import json
    import math
    import re as _re

    ak, url, m = _resolve_llm_or_explicit(
        model_env_vars=(
            "YOUTOBE_LLM_DUB_DURATION_MODEL",
            "OPENAI_DUB_DURATION_FIT_MODEL",
            "OPENAI_COLLOQUIAL_MODEL",
            "YOUTOBE_LLM_COLLOQUIAL_MODEL",
            "OPENAI_TRANSLATION_MODEL",
            "DEEPSEEK_TRANSLATION_MODEL",
        ),
        api_key=api_key,
        base_url=base_url,
        model=model,
        fallback_openai_model="gpt-4o-mini",
    )
    try:
        from subtitle_reading_time import zh_timing_units as _zh_u
    except ImportError:

        def _zh_u(t: str) -> float:  # type: ignore[misc]
            return max(0.2, len((t or "").strip()))

    sys_prompt = (
        "You compress Simplified Chinese dubbing lines for neural TTS. "
        "Same count and order as input items. Output ONLY JSON: {\"lines\":[\"...\",...]} . "
        "Each input item has: English subtitle line (timing reference), Chinese translation, "
        "slot_seconds = how long that English line lasts in the film, plus zh_units_now and timing_budget_units. "
        "zh_units_now / timing_budget_units follow the same timing-unit model as subtitle_reading_time.py "
        "(CJK-heavy load; not raw character count). "
        "Rewrite Chinese so estimated units at natural speech would be at or below timing_budget_units "
        f"(CPS budget ≈ {chars_per_sec_budget:.2f} units per second of slot). "
        "Preserve core meaning vs the Chinese line and facts implied by English; drop redundancy, "
        "avoid long subordinate clauses; use oral concise Chinese. "
        "Place commas ，、 where short pauses help; avoid semicolons; no newlines in strings; "
        "no parentheses/stage directions; no English except unavoidable proper nouns; no markdown. "
        "End on a complete phrase when possible (avoid cutting the last syllable off in TTS—"
        "no orphan 的/了/吗 alone at the end). "
        "If Chinese is already short enough, return it lightly polished only."
    )
    payload_items = [
        {
            "en": en,
            "zh": zh,
            "slot_sec": round(sec, 3),
            "zh_units_now": round(_zh_u(zh), 2),
            "timing_budget_units": round(max(0.35, sec * chars_per_sec_budget * 0.98), 2),
            "max_chars_hint": max(4, int(math.ceil(sec * chars_per_sec_budget * 1.05))),
        }
        for en, zh, sec in items
    ]
    user_payload = json.dumps({"items": payload_items}, ensure_ascii=False)
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            content = _llm_post_chat_content(
                chat_url=url,
                api_key=ak,
                model=m,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.25,
                timeout=timeout,
            )
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"lines\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析大模型返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("lines")
            if not isinstance(arr, list) or len(arr) != len(items):
                raise RuntimeError(
                    f"大模型时长适配返回条数 {len(arr) if isinstance(arr, list) else '?'} "
                    f"与输入 {len(items)} 不一致"
                )
            return [str(x).strip() for x in arr]
        except Exception as e:
            last_err = e
            time.sleep(1.0 + attempt * 1.8)
    raise RuntimeError(f"配音时长适配（大模型）失败: {last_err}") from last_err


def zh_reading_time_align_batch(
    items: list[tuple[str, str, float, str]],
    *,
    api_key: str | None = None,
    chars_per_sec_budget: float | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 130.0,
) -> list[str]:
    """
    翻译后：按英文字幕槽位 slot_sec，把中文稿的「预估朗读负载」拉到合理区间。
    items: (english_line, chinese_line, slot_seconds, issue)，issue 为 \"over\" 或 \"under\"。
    over: 压缩中文，避免配音吞字；under: 略作充实口语化，减少句内过长留白（不灌水）。
    """
    if not items:
        return []
    import json
    import re as _re

    try:
        from subtitle_reading_time import zh_timing_units
    except ImportError:
        def zh_timing_units(t: str) -> float:  # type: ignore[misc]
            return max(0.2, len((t or "").strip()))

    cps = chars_per_sec_budget
    if cps is None or cps <= 0:
        cps = float(os.getenv("YOUTOBE_TRANSLATE_READING_CPS", "").strip() or 0) or float(
            os.getenv("YOUTOBE_DUB_CPS_TARGET", "3.45").strip() or "3.45"
        )
    cps = max(2.2, min(float(cps), 5.5))

    ak, url, m = _resolve_llm_or_explicit(
        model_env_vars=(
            "YOUTOBE_LLM_READING_ALIGN_MODEL",
            "YOUTOBE_LLM_TRANSLATION_MODEL",
            "OPENAI_TRANSLATION_MODEL",
            "DEEPSEEK_TRANSLATION_MODEL",
        ),
        api_key=api_key,
        base_url=base_url,
        model=model,
        fallback_openai_model="gpt-4o-mini",
    )
    sys_prompt = (
        "You edit Simplified Chinese SUBTITLE lines for spoken pacing vs English on-screen duration. "
        "The goal is Chinese neural TTS dubbing that fits the same time window as each English line: "
        "avoid rushed speech (over) and avoid long empty air inside the cue (under), which causes A/V feel desync. "
        "Input JSON has \"items\": array in order; each has: en, zh, slot_sec, issue (over|under), "
        "zh_units_now, est_sec_at_cps, target_units_lo, target_units_hi, target_est_sec_lo, target_est_sec_hi. "
        "Timing units match subtitle_reading_time.py (CJK-heavy load); est_sec_at_cps = zh_units_now / CPS. "
        "- If issue is over: shorten zh so new zh_units would be ≤ target_units_hi (ideally est_sec ≤ target_est_sec_hi), "
        "without losing core meaning vs English; oral concise Chinese; one line per item. "
        "- If issue is under: expand zh with natural connectives so new zh_units moves into "
        "[target_units_lo, target_units_hi] (spoken est near target_est_sec_hi without exceeding slot_sec). "
        "Do not add filler 呃嗯啊, no meta, no newlines. Preserve names/brands. Same number of items and order. "
        'Output ONLY JSON: {"lines":["...","..."]} with len(lines)==len(items).'
    )
    payload_items: list[dict[str, object]] = []
    for en, zh, slot, issue in items:
        slot = max(0.05, float(slot))
        mid = slot * cps
        u_now = zh_timing_units(zh)
        est = u_now / cps if cps > 1e-6 else 0.0
        if issue == "over":
            lo = max(0.8, mid * 0.42)
            hi = max(lo + 0.25, mid * 0.92)
            tlo = lo / cps if cps > 1e-6 else 0.0
            thi = hi / cps if cps > 1e-6 else 0.0
        else:
            lo = max(1.0, mid * 0.68)
            hi = max(lo + 0.45, mid * 0.97)
            tlo = lo / cps if cps > 1e-6 else 0.0
            thi = min(slot * 0.99, hi / cps if cps > 1e-6 else slot)
        payload_items.append(
            {
                "en": en or " ",
                "zh": zh or " ",
                "slot_sec": round(slot, 4),
                "issue": issue,
                "zh_units_now": round(u_now, 2),
                "est_sec_at_cps": round(est, 3),
                "target_units_lo": round(lo, 2),
                "target_units_hi": round(hi, 2),
                "target_est_sec_lo": round(tlo, 3),
                "target_est_sec_hi": round(thi, 3),
            }
        )
    user_payload = json.dumps({"items": payload_items}, ensure_ascii=False)
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            content = _llm_post_chat_content(
                chat_url=url,
                api_key=ak,
                model=m,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.28,
                timeout=timeout,
            )
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"lines\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析大模型返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("lines")
            if not isinstance(arr, list) or len(arr) != len(items):
                raise RuntimeError(
                    f"朗读对齐返回条数 {len(arr) if isinstance(arr, list) else '?'} "
                    f"与输入 {len(items)} 不一致"
                )
            return [str(x).strip() for x in arr]
        except Exception as e:
            last_err = e
            time.sleep(1.0 + attempt * 1.8)
    raise RuntimeError(f"翻译朗读时长对齐（大模型）失败: {last_err}") from last_err


def dub_delivery_style_batch(
    pairs: list[tuple[str, str]],
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 130.0,
) -> list[dict[str, float]]:
    """
    根据英中字幕对推断「朗读表达强度」，输出可映射到 Edge / ElevenLabs / Fish Speech 的数值参数。
    pairs: (english_cue, chinese_dub_line)，与配音段顺序一致。
    返回每项含 rate_delta_pct, pitch_delta_hz, eleven_stability, eleven_similarity_boost。
    """
    if not pairs:
        return []
    import json
    import re as _re

    try:
        from dub_delivery_map import normalize_delivery_row
    except ImportError:
        normalize_delivery_row = None  # type: ignore[misc, assignment]

    def _neutral(_: dict) -> dict[str, float]:
        return {
            "rate_delta_pct": 0.0,
            "pitch_delta_hz": 0.0,
            "eleven_stability": 0.5,
            "eleven_similarity_boost": 0.75,
        }

    ak, url, m = _resolve_llm_or_explicit(
        model_env_vars=(
            "YOUTOBE_LLM_DUB_EMOTION_MODEL",
            "YOUTOBE_LLM_COLLOQUIAL_MODEL",
            "OPENAI_COLLOQUIAL_MODEL",
            "YOUTOBE_LLM_TRANSLATION_MODEL",
            "OPENAI_TRANSLATION_MODEL",
            "DEEPSEEK_TRANSLATION_MODEL",
        ),
        api_key=api_key,
        base_url=base_url,
        model=model,
        fallback_openai_model="gpt-4o-mini",
    )
    sys_prompt = (
        "You infer spoken delivery for Chinese TTS dubbing so it tracks the English speaker's energy and avoids a flat monotone. "
        'Input JSON: {"pairs":[{"en":"...","zh":"..."},...]} in order (same length as output). '
        "Use punctuation, caps, rhetorical questions, humor, urgency, empathy, irony, contrast, and emphasis in EN "
        "to decide how animated the Chinese line should sound when read aloud. "
        "Vary parameters across lines: consecutive lines should NOT all get identical numbers unless the script is uniformly flat. "
        "Stronger EN cues (exclamations, questions, sudden turns) → larger |rate_delta_pct| and |pitch_delta_hz|, "
        "lower eleven_stability, and slightly lower eleven_similarity_boost for more color. "
        "Calm factual EN → smaller deltas, stability nearer mid. "
        "Output ONLY JSON: {\"lines\":[{\"rate_delta_pct\":n,\"pitch_delta_hz\":n,"
        "\"eleven_stability\":n,\"eleven_similarity_boost\":n},...]} with len(lines)==len(pairs). "
        "Fields (numbers): rate_delta_pct in [-12,12] added on top of baseline Edge rate %; "
        "pitch_delta_hz in [-10,10] added to baseline Hz; eleven_stability 0.22–0.78 (lower=more expressive); "
        "eleven_similarity_boost 0.52–0.92. Use zeros and 0.5/0.75 only when both EN and zh are genuinely flat neutral."
    )
    user_payload = json.dumps(
        {
            "pairs": [
                {"en": (en or " ").strip(), "zh": (zh or " ").strip()}
                for en, zh in pairs
            ]
        },
        ensure_ascii=False,
    )
    t_inf = float(os.getenv("YOUTOBE_DUB_EMOTION_TEMP", "").strip() or "0.42")
    t_inf = max(0.15, min(t_inf, 0.75))
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            content = _llm_post_chat_content(
                chat_url=url,
                api_key=ak,
                model=m,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=t_inf,
                timeout=timeout,
            )
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                mm = _re.search(r"\{[\s\S]*\"lines\"[\s\S]*\}", content)
                if not mm:
                    raise RuntimeError(
                        f"无法解析大模型返回: {content[:400]}"
                    ) from e
                parsed = json.loads(mm.group(0))
            arr = parsed.get("lines")
            if not isinstance(arr, list) or len(arr) != len(pairs):
                raise RuntimeError(
                    f"情绪韵律返回条数 {len(arr) if isinstance(arr, list) else '?'} "
                    f"与输入 {len(pairs)} 不一致"
                )
            out: list[dict[str, float]] = []
            for it in arr:
                if isinstance(it, dict) and normalize_delivery_row is not None:
                    rd, ph, st, sim = normalize_delivery_row(it)
                    out.append(
                        {
                            "rate_delta_pct": rd,
                            "pitch_delta_hz": ph,
                            "eleven_stability": st,
                            "eleven_similarity_boost": sim,
                        }
                    )
                else:
                    out.append(_neutral({}))
            return out
        except Exception as e:
            last_err = e
            time.sleep(1.0 + attempt * 1.8)
    raise RuntimeError(f"配音情绪/韵律推断（大模型）失败: {last_err}") from last_err


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
