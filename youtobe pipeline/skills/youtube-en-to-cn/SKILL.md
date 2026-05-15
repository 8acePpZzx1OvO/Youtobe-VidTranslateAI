---
name: youtube-en-to-cn
description: >
  Youtobe Pro：下载、翻译、双语 SRT、中文配音（Edge / 火山 / ElevenLabs）、可选本地 Whisper 英文字幕、
  默认硬烧双语+中文配音成片；`--finalize-only` 补全缺失步骤。
allowed-tools:
  - Read
  - Write
  - Bash
  - Glob
---

# Youtobe Pro 流水线

本仓库中译制入口位于 **`youtobe/`** 子目录：在 `youtobe` 下执行 `python run.py ...`，或在仓库根执行 `python youtobe/run.py ...`。环境变量文件为 **`youtobe/.env`**（由 `youtobe/env.example` 复制）。

融合自 [video-Zebra-china](https://github.com/jiayuqi7813/video-Zebra-china) 的火山 TTS、[ai-dubbing](https://github.com/jin-wook-lee-96/ai-dubbing) 的 **ElevenLabs eleven_multilingual_v2** 与 **faster-whisper** 本地 ASR；翻译见 `translate_srt.py`（**机翻 + 大模型润色** 或单一后端，见 `env.example`）。

## 首选命令

```bash
cd youtobe
python run.py "<YouTube_URL>" --full
```

复制 `env.example` 为 `.env`（在 `youtobe` 目录内）。翻译可填 **YOUTOBE_LLM_API_KEY**（国内兼容接口，推荐）、**DEEPSEEK_API_KEY**、**DEEPL_API_KEY** / **MICROSOFT_API_KEY**、旧版 **OPENAI_API_KEY** 等。

## 火山中文配音（高质量商用 TTS）

`.env` 配置 `VOLCENGINE_TTS_API_KEY`（及可选 `VOLCENGINE_TTS_RESOURCE_ID`），然后：

```bash
python run.py "<URL>" --full --dub-backend volc --dub-voice zh_female_qingxin --dub-concurrency 3
```

`--finalize-only <视频ID>` 同样支持 `--dub-backend volc|elevenlabs|edge`。

## ElevenLabs 配音（与 [ai-dubbing](https://github.com/jin-wook-lee-96/ai-dubbing) 同款）

`.env` 配置 `ELEVENLABS_API_KEY`，可选 `ELEVENLABS_VOICE_ID`。`auto` 时：**火山 Key > ElevenLabs Key > Edge**。

```bash
python run.py "<URL>" --full --dub-backend elevenlabs --dub-concurrency 2
```

## 无 YouTube 官方字幕时

先安装 Pro 依赖：在仓库根 `pip install -r youtobe/requirements-pro.txt`，再：

```bash
python run.py "<URL>" --full --asr-whisper --whisper-model small
```

## 续译 / 仅补成片

```bash
python run.py "<同一URL>" --full --resume
python run.py --finalize-only <视频ID>
```

成片：`youtobe/output/processed/<id>/<id>_zh_dub_hard_bilingual.mp4`（硬烧中英+中文配音）、同目录下软字幕版。

## 参数入口

`python run.py -h`；`python scripts/dub_zh.py -h`；`python scripts/translate_srt.py -h`。

## 合规

仅处理有权使用的素材。
