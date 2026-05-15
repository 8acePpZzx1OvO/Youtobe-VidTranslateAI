# Youtobe

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-use%20responsibly-orange)

**本机一站式：YouTube 下载 → 英文字幕（或 Whisper）→ 中译 → 双语字幕 → 中文配音 → 软/硬成片；可选在成片后再导出观看倍速版。**

输出默认在 `output/raw/<视频ID>/` 与 `output/processed/<视频ID>/`。翻译与 TTS 可走多家云厂商或免费兜底，详见下文与 `env.example`。

---

## 为什么选择 Youtobe？

| 能力 | 说明 |
|------|------|
| **流程一体** | 一条 `run.py` 命令串起下载、翻译、配音、封装、硬烧，少手工拼接 FFmpeg。 |
| **对齐口播与画面** | 默认按**英文字幕时间轴**对齐中文 TTS，并合并双语 SRT 供硬烧，减轻「说下一句了画面还在上一句」的错位。 |
| **无官方字幕也能跑** | 视频没有 YouTube 字幕轨时，加 `--asr-whisper` 用本地 **faster-whisper** 生成英文稿再翻译（见 `requirements-pro.txt`）。 |
| **倍速不破坏译配** | `--video-speed` 在 **1.0× 译配成片完成之后** 再对 MP4 做整片 `setpts` + `atempo`，**不缩放** `en/zh/bilingual` 的 SRT 时间轴；硬烧成片字幕在画面内，与画面同倍速。 |
| **FFmpeg 零配置可选** | 安装 `requirements-pro.txt` 中的 **static-ffmpeg** 后，可在未单独安装系统 FFmpeg 的情况下完成抽音频、封装与硬烧（仍建议生产环境安装系统 FFmpeg）。 |

---

## 快速开始

### 1. 克隆与虚拟环境

```powershell
git clone https://github.com/<你的用户名>/Youtobe.git
cd Youtobe
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-pro.txt
```

**说明：** `requirements-pro.txt` 在基础包之上增加 **faster-whisper**（无字幕时 ASR）与 **static-ffmpeg**（自带 `ffmpeg`/`ffprobe`，供 pydub 与部分脚本使用）。只做「有字幕、不配音」时可只装 `requirements.txt`，但推荐一次性装齐。仓库**不包含**本地目录 `.tools/`（若你自行解压过便携 FFmpeg，勿提交；已在 `.gitignore` 中排除）。

### 2. 配置环境变量

```powershell
copy env.example .env
# 用编辑器打开 .env，至少配置一种翻译 Key（见下文「环境变量」）
```

### 3. 一条命令全流程（推荐）

```powershell
python run.py "https://www.youtube.com/watch?v=视频ID" --full
```

- **`--full`**：等价 `--bilingual --dub-zh`，生成双语 SRT、中文配音、软字幕 MP4 与**硬烧双语+中文配音**成片（默认开启硬烧）。

**无 YouTube 字幕时**（yt-dlp 提示没有字幕轨）：

```powershell
python run.py "https://youtu.be/视频ID" --full --asr-whisper
```

**成片后再导出 1.5× 观看版**（额外生成 `*_x1p5.mp4` 等，**不修改** SRT）：

```powershell
python run.py "https://youtu.be/视频ID" --full --video-speed 1.5
```

**已有 `en.srt` / `zh.srt`，只补配音与成片：**

```powershell
python run.py --finalize-only 视频ID
```

---

## 流水线一览（执行顺序）

| 阶段 | 做什么 | 主要产物 |
|------|--------|----------|
| 1. 下载 | yt-dlp 拉取 MP4（及可用时的英文字幕 VTT） | `output/raw/<id>/<id>.mp4` |
| 2. 英文字幕 | VTT→SRT；无字幕且 `--asr-whisper` 时用 Whisper 写 `en.srt` | `output/processed/<id>/<id>.en.srt` |
| 3. 翻译 | `translate_srt.py`，支持断点 `--resume` | `*.zh.srt` |
| 4. 配音 | `dub_zh.py`（Edge / 火山 / ElevenLabs 等） | `*.dub_zh.m4a`、`*.zh.dubsync.srt`（若启用对齐） |
| 5. 双语条 | `merge_bilingual_srt.py` | `*.bilingual.srt` |
| 6. 封装 | `mux_dub_subs.py`（可选软字幕轨） | `*_zh_dub_softsubs.mp4` |
| 7. 硬烧 | `burn_subtitles.py` | `*_zh_dub_hard_bilingual.mp4`（推荐分享） |
| 8. 倍速（可选） | `--video-speed ≠ 1.0` 时对**成片**再编码 | `*_x…*.mp4`（见 `scripts/apply_video_playback_speed.py`） |

---

## 默认输出路径

| 路径 | 说明 |
|------|------|
| `output/raw/<id>/<id>.mp4` | 原始下载（含原声） |
| `output/processed/<id>/<id>.en.srt` | 英文 |
| `output/processed/<id>/<id>.zh.srt` | 中文 |
| `output/processed/<id>/<id>.bilingual.srt` | 中英双语（可外挂） |
| `output/processed/<id>/<id>.dub_zh.m4a` | 仅中文配音轨 |
| `output/processed/<id>/<id>_zh_dub_softsubs.mp4` | 画面 + 中文配音 + **软字幕**（依赖播放器） |
| `output/processed/<id>/<id>_zh_dub_hard_bilingual.mp4` | **推荐成片**：硬烧双语 + 中文配音 |

---

## `run.py` 命令参考

| 参数 | 说明 |
|------|------|
| `url` | YouTube 链接；与 `--finalize-only` 二选一场景下可省略 |
| `--raw-dir` / `--proc-dir` | 原始 / 处理后根目录，默认 `output/raw`、`output/processed` |
| `--full` | 双语 + 中文配音 + 默认硬烧成片 |
| `--bilingual` | 仅生成双语 SRT（不强制配音） |
| `--dub-zh` | 中文配音与封装（常与 `--bilingual` 或 `--full` 同用） |
| `--finalize-only <视频ID>` | 跳过下载与翻译，从已有 mp4 + en/zh 补配音与成片 |
| `--resume` | 翻译断点续跑 |
| `--translate-engine` | `smart`（默认）或 `deepl` / `azure` / `openai` / `aliyun` / `tencent` / `google` / `mymemory` / `auto` |
| `--asr-whisper` | 无 YouTube 字幕时用 faster-whisper 识别英文 |
| `--whisper-model` | 与 ASR 联用，默认 `small` |
| `--video-speed N` | 成片后再导出 N 倍速 MP4；`1.0` 不导出；可用 `YOUTOBE_VIDEO_SPEED` |
| `--no-soft-subs` | 不封装软字幕轨（仍有硬烧与外挂 srt） |
| `--no-hard-bilingual` | 不生成硬烧双语成片 |
| `--burn` | 在**原片**上硬烧**中文**字幕 |
| `--burn-bilingual` | 在**原片**上硬烧**双语**字幕 |
| `--dub-backend` | `auto` / `edge` / `volc` / `elevenlabs` |
| `--dub-voice` | 音色（Edge 或火山等，见帮助） |
| `--dub-concurrency` | TTS 并发数 |
| `--dub-merge-repeats` / `--no-dub-merge-repeats` | 是否合并相邻重复句（配音） |
| `--dub-sync-en-time` / `--no-dub-sync-en-time` | 是否与英文字幕时间对齐（默认开） |
| `--dub-duration-fit-openai` / `--no-dub-duration-fit-openai` | 是否用 OpenAI 按英文句长压缩中文口播（默认开，需 Key） |
| `--dub-colloquial-openai` | 口语化旁白（需 OpenAI） |
| `--dub-tts-polish-openai` | TTS 标点润色（需 OpenAI） |
| `--dub-max-speedup` | 对齐时最大变速倍数 |
| `--dub-edge-rate` / `--dub-edge-pitch` | Edge 语速 / 音高 |
| `--dub-cps-target` | 口播密度（字/秒） |
| `--dub-en-srt` | 显式指定英文 SRT 路径 |
| `--allow-incomplete-zh` | 中文条数少于英文时仍继续（慎用） |
| `--speech-smooth` | 翻译口语顺化 |
| `--sleep` | 翻译每条间隔（秒），防限流 |
| `--batch-lines` / `--batch-chars` | 翻译批大小 |

更细的子脚本说明见 `python scripts/<脚本>.py -h`。

---

## 环境变量（`.env`）

完整模板见 **`env.example`**。常用项：

- **翻译**：`DEEPL_API_KEY`、`MICROSOFT_API_KEY` + `AZURE_TRANSLATOR_REGION`、`OPENAI_API_KEY`、阿里云、腾讯云等（`smart` 优先级见 `env.example` 顶部注释）。
- **下载**：`YOUTOBE_YTDLP_PROXY`、`YOUTOBE_YTDLP_CONCURRENT_FRAGMENTS` 等。
- **配音**：`YOUTOBE_EDGE_TTS_PROXY`（国内访问 Edge TTS 常需代理）、火山 / ElevenLabs Key。
- **成片倍速**：`YOUTOBE_VIDEO_SPEED`（与 `--video-speed` 一致，默认 `1.0`）。

---

## 分步脚本（排错）

```powershell
python scripts/download.py "<URL>" -o output/raw
python scripts/vtt_to_srt.py output/raw/<id>/<id>.en.vtt output/processed/<id>/<id>.en.srt
python scripts/translate_srt.py output/processed/<id>/<id>.en.srt output/processed/<id>/<id>.zh.srt --engine smart --resume
python scripts/merge_bilingual_srt.py output/processed/<id>/<id>.en.srt output/processed/<id>/<id>.zh.srt output/processed/<id>/<id>.bilingual.srt
python scripts/dub_zh.py output/raw/<id>/<id>.mp4 output/processed/<id>/<id>.zh.srt output/processed/<id>/<id>.dub_zh.m4a
python scripts/mux_dub_subs.py output/raw/<id>/<id>.mp4 output/processed/<id>/<id>.dub_zh.m4a output/processed/<id>/<id>_zh_dub_softsubs.mp4 --subs output/processed/<id>/<id>.bilingual.srt
```

---

## 下载失败（SSL / 403 / 代理）

- 在 `.env` 中配置 **`YOUTOBE_YTDLP_PROXY`** 或系统 `HTTPS_PROXY`。
- 保持 **`YOUTOBE_YTDLP_CONCURRENT_FRAGMENTS=1`** 通常更稳。
- 若遇 YouTube **PO Token** 相关提示，见 [yt-dlp PO Token 指南](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)。
- 可安装 **Node / Deno** 以改善部分站点的 JS 挑战提取，见 [yt-dlp EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS)。

---

## 推送到 GitHub（**私密**仓库）

当前环境未检测到 **`gh` CLI**，且推送必须使用**你的** GitHub 凭据，无法由助手代你完成登录。任选其一即可。

### 方式 A：网页创建 + Git 命令行

1. 打开 [GitHub → New repository](https://github.com/new)，仓库名例如 `Youtobe`，勾选 **Private**，**不要**勾选「Add a README」（本地已有）。
2. 在项目根执行（将 `你的用户名` 换成实际 GitHub 用户名）：

```powershell
cd F:\project\Youtobe
git init
git add -A
git commit -m "Initial commit: Youtobe pipeline and documentation"
git branch -M main
git remote add origin https://github.com/你的用户名/Youtobe.git
git push -u origin main
```

若已开启 2FA，请使用 **Personal Access Token** 代替密码。

### 方式 B：GitHub CLI（一条命令创建私密库并推送）

```powershell
winget install GitHub.cli
gh auth login
cd F:\project\Youtobe
git init
git add -A
git commit -m "Initial commit: Youtobe pipeline and documentation"
gh repo create Youtobe --private --source=. --remote=origin --push
```

---

## Claude Code Skill（可选）

将 `skills/youtube-en-to-cn` 复制到 `%USERPROFILE%\.claude\skills\youtube-en-to-cn` 即可在 Claude Code 中引用。

---

## 合规说明

请只处理你有权使用的视频，并遵守 [YouTube 服务条款](https://www.youtube.com/static?template=terms) 与当地法律。配音为 TTS 合成，与真人译制成片在表现力上存在差异，属正常现象。
