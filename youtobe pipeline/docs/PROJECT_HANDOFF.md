# 项目交接文档（Living Handoff）

> **用途**：新开 Cursor 窗口时，优先读本文件了解仓库结构、主流程、关键模块与最近变更。  
> **维护规则**：每次功能优化或新增后，**必须**在本文件顶部「最近变更」追加一条，并同步更新相关章节。  
> **历史详录**：更早的逐轮对话见 `SESSION_HANDOFF_对话与任务全记录.md`（只增不改，作档案）。

**最后更新**：2026-05-16  
**维护者**：与用户结对开发的 Agent（按用户要求自动维护）

---

## 最近变更（倒序，新条目插在最上）

### 2026-05-16 — 配音时间轴：去重叠 + 超长句切分（修复 60–90s 滔滔不绝/长静音）

| 项 | 内容 |
|----|------|
| **问题** | 例 `8W2Zw8Cijo8` 第 15 条 en/zh 字幕重叠且槽位 ~25s，整段中文一次 TTS 念完 → 60–80s 连读、80–90s 静音。 |
| **修复** | `dub_zh.py`：`_deoverlap_dub_segments`、`_split_oversize_dub_segments`、`_cap_slot_no_overlap`；口播 fit 仅用字幕可见时长，句间留白用静音。 |
| **修改** | `en_sentence_merge_srt.py` 默认 `max_span_ms` 18s→11s；`feature_defaults.env` 增加 `YOUTOBE_DUB_MAX_SLOT_MS` 等。 |

### 2026-05-16 — 回退「性能优化」相关改动（用户要求恢复质量）

| 项 | 内容 |
|----|------|
| **动机** | 性能优化及后续对齐修补后出现配音过快、句尾吞字等问题；用户要求回退到优化速度之前的配音/封装/硬烧逻辑。 |
| **回退** | 删除 `pipeline_defaults` 性能辅助用法；`burn_subtitles.py` / `mux_dub_subs.py` / `dub_zh.py` / `translate_srt.py` / `run.py` 恢复优化前行为（含 tail grace、max_speedup 1.22、`--sleep` 0.25）。 |
| **保留** | Fish Speech 集成、ChatTTS 移除、交接文档规范等**非性能**改动仍在。 |
| **注意** | 若 `.env` 中曾手动写入 `YOUTOBE_MUX_AUDIO_COPY` / `YOUTOBE_STRICT_SUB_SLOT` 等，请删除或注释。 |

### 2026-05-16 — Fish Speech 配音接入；移除 ChatTTS

| 项 | 内容 |
|----|------|
| **动机** | ChatTTS 在 Python 3.14 上依赖 `pybase16384` 等易失败；改用 [fish-speech](https://github.com/fishaudio/fish-speech) HTTP API 做本地中文配音。 |
| **新增** | `scripts/fish_speech_tts_client.py` — 调用官方 `POST /v1/tts?format=msgpack`，健康检查 `GET /v1/health`。 |
| **修改** | `scripts/dub_zh.py` — `--backend fish`；`auto` + `YOUTOBE_DUB_PREFER_FISH_SPEECH=1` 优先 Fish；Fish 不可用时降级 volc → eleven → edge。 |
| **修改** | `run.py`、`finish_outputs.py` — `--dub-backend` 增加 `fish`，移除 `chattts`。 |
| **修改** | `env.example` — Fish Speech 变量块；`requirements.txt` — `ormsgpack` / `msgpack`。 |
| **删除** | `scripts/chattts_tts_client.py`、`requirements-chattts.txt`。 |
| **上游克隆** | 建议 `vendor/fish-speech`（已在根 `.gitignore` 的 `vendor/**` 下，不进库）。启动：`python tools/api_server.py --listen 127.0.0.1:8888`。 |
| **验证** | 已对 `8W2Zw8Cijo8` 跑通 `--full`（Edge 配音，因未启 Fish 服务）。 |

### 2026-05-16 — 建立交接文档规范

- 新增本文件 `docs/PROJECT_HANDOFF.md` 与 Cursor 规则 `.cursor/rules/project-handoff.mdc`。
- 约定：此后每次功能改动结束任务前更新「最近变更」与受影响模块说明。

---

## 1. 仓库一览

```
F:/project/Youtobe/
├── README.md                          # 仓库总览（路径以根为准，部分文档仍写 youtobe/ 旧名）
├── PROJECT_LAYOUT.md                  # 目录索引（指向本交接文档）
├── video_fetcher/                     # 批量拉取 YouTube 列表并调用 pipeline
│   ├── cli.py, batch.py
│   └── youtobe_runner.py              # subprocess 调 youtobe pipeline/run.py
├── vendor/                            # 第三方克隆（gitignore），如 fish-speech
└── youtobe pipeline/                  # ★ 译配主流水线（工作目录常 cd 到此）
    ├── run.py                         # ★ 一键入口
    ├── finish_outputs.py → scripts/finish_outputs.py
    ├── env.example / .env
    ├── config/feature_defaults.env
    ├── output/raw/<视频ID>/           # 原片 mp4、vtt
    ├── output/processed/<视频ID>/     # en/zh SRT、配音、成片
    ├── docs/
    │   ├── PROJECT_HANDOFF.md         # ★ 本文件
    │   └── SESSION_HANDOFF_对话与任务全记录.md
    └── scripts/                       # 各步骤脚本（run.py 子进程调用）
```

**用户偏好**：中文简体回复；未明确要求不要 git commit；重视配音/翻译质量。

---

## 2. 主流程（run.py）

```
下载 (download.py)
  → VTT→SRT (vtt_to_srt.py)
  → 英文滚动去重 (dedupe_rolling_srt.py) + 句合并 (en_sentence_merge_srt.py)
  → 英译中 (translate_srt.py → translation_clients.py)
  → [可选] 双语 SRT (merge_bilingual_srt.py)
  → 中文配音 (dub_zh.py)
  → 封装软字幕 (mux_dub_subs.py) + 硬烧双语 (burn_subtitles.py)
  → [可选] 倍速导出 (apply_video_playback_speed.py)
  → [默认] 精简中间产物 (minimize_pipeline_outputs.py / youtobe_layout.py)
```

**常用命令**（在 `youtobe pipeline/` 下）：

```powershell
# 全流程
python run.py "https://www.youtube.com/watch?v=VIDEO_ID" --full

# 已有 raw mp4 + en/zh SRT，只补配音与成片
python run.py --finalize-only VIDEO_ID

# Fish Speech 配音（需先启动 api_server）
python run.py --finalize-only VIDEO_ID --dub-backend fish --dub-concurrency 1
```

---

## 3. 关键脚本职责

| 文件 | 作用 |
|------|------|
| `run.py` | CLI 编排；加载 `.env`；subprocess 调 scripts |
| `scripts/translate_srt.py` | 字幕翻译 CLI；引擎 smart/deepseek/openai/volc；朗读时长对齐 |
| `scripts/translation_clients.py` | LLM 翻译/润化/口播压缩/情绪 delivery 批量推断 |
| `scripts/subtitle_reading_time.py` | CPS、over/under 判定（翻译与配音共用） |
| `scripts/dub_zh.py` | ★ 中文配音轨：时间轴对齐、TTS 多后端、情绪映射、ffmpeg atempo |
| `scripts/dub_delivery_map.py` | delivery 数值夹紧 |
| `scripts/finish_outputs.py` | finalize-only 子流程 |
| `scripts/youtobe_layout.py` | raw/processed 路径解析、输出精简开关 |
| `pipeline_defaults.py` | 预留占位（当前无逻辑引用） |
| `scripts/download.py` | yt-dlp 下载视频+字幕 |
| `scripts/mux_dub_subs.py` | 视频+配音+字幕封装 |
| `scripts/burn_subtitles.py` | 硬烧字幕 |

---

## 4. 配音后端（dub_zh.py --backend）

| 后端 | 说明 | 主要依赖 / 环境变量 |
|------|------|---------------------|
| `edge` | 默认兜底 | `edge-tts`；国内常需 `YOUTOBE_EDGE_TTS_PROXY` |
| `volc` | 火山 OpenSpeech | `VOLCENGINE_TTS_API_KEY`、`VOLC_TTS_VOICE` |
| `elevenlabs` | 多语言 TTS | `ELEVENLABS_API_KEY`、`ELEVENLABS_VOICE_ID` |
| `fish` | Fish Speech HTTP | `YOUTOBE_FISH_SPEECH_URL`（默认 `http://127.0.0.1:8888`）、`YOUTOBE_FISH_SPEECH_REFERENCE_ID` |
| `auto` | 优先级链 | `YOUTOBE_DUB_PREFER_FISH_SPEECH=1` 且 HTTP 可用 → fish；否则 volc key → eleven key → edge |

**情绪对齐**（`--emotion-align`，默认开）：`dub_delivery_style_batch` 推断每条 rate/pitch/stability → 映射 Edge/Eleven 参数或 Fish 中文前缀。

**已移除**：ChatTTS 进程内推理（`chattts_tts_client.py` 已删）。

---

## 5. 环境与配置

- **复制模板**：`copy env.example .env`（在 `youtobe pipeline/` 下）
- **默认特性**：`config/feature_defaults.env`（不覆盖已有 env）
- **LLM**：`YOUTOBE_LLM_*` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`（翻译、口播化、时长压缩、情绪推断）
- **Fish Speech**：见 `env.example` 中 `YOUTOBE_FISH_SPEECH_*` 块
- **输出精简**：默认成功后只留 en/zh SRT + 硬烧成片；调试 `--keep-intermediate` 或 `YOUTOBE_MINIMIZE_OUTPUTS=0`

---

## 6. 输出产物命名

| 文件 | 含义 |
|------|------|
| `{id}.en.srt` / `{id}.zh.srt` | 英/中字幕 |
| `{id}.zh.dubsync.srt` | 与配音口播对齐后的中文稿 |
| `{id}.dub_zh.m4a` | 中文配音轨 |
| `{id}.bilingual.srt` | 双语字幕 |
| `{id}_zh_dub_softsubs.mp4` | 软字幕+中文配音 |
| `{id}_zh_dub_hard_bilingual.mp4` | ★ 推荐成片：硬烧双语+中文配音 |

---

## 7. 待办 / 已知局限

- Fish Speech 需用户自行克隆权重并启动 `api_server`；未启动时 `--backend fish` 会降级，不会静默失败。
- 火山 TTS 仍无逐条情感 API（仅日志提示）。
- `PROJECT_LAYOUT.md`、根 README 部分路径仍写旧目录名 `youtobe/`，实际目录为 `youtobe pipeline/`。
- `dub_theme_profile.py` / `config/dub_theme_profiles.yaml` 为主题预设占位，尚未接入主流程。

---

## 8. 交接文档更新模板（复制用于下次变更）

```markdown
### YYYY-MM-DD — 简短标题

| 项 | 内容 |
|----|------|
| **动机** | 用户要什么 / 解决什么问题 |
| **新增** | 新文件、新 CLI 参数、新 env |
| **修改** | 改动的模块与行为变化 |
| **删除** | 移除的文件或废弃能力 |
| **验证** | 跑过的命令、视频 ID、结果路径 |
| **注意** | 破坏性变更、迁移步骤、已知 bug |
```

更新后同步修改本文 **§3 关键脚本**、**§4 配音后端**、**§7 待办** 中相关段落（如有影响）。
