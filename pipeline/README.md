# pipeline — [VideoLingo](https://github.com/Huanshere/VideoLingo)

本目录为 **VideoLingo** 上游代码 + 薄适配层 [`bridge/`](bridge/)，供 `video_fetcher` / `content_hub` 以统一路径调用。

## 安装

```powershell
cd F:\project\youtube-vid-translate
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[pipeline,content-hub]"
pip install -r pipeline/requirements.txt
python pipeline/install.py
```

**务必先激活 `.venv`**，否则会用到系统 Python，出现 `No module named 'ruamel'` 等错误。

`install.py` 会安装 PyTorch、WhisperX、Demucs 等（见 [VideoLingo 文档](https://github.com/Huanshere/VideoLingo)）。需本机 **FFmpeg**。

## 配置

1. 复制 `env.example` → `.env`（翻译 API、代理）
2. 编辑 `config.yaml`（VideoLingo 主配置；`run.py` 会将 `.env` 中 DeepSeek 等写入 `api` 段）

## 命令

```powershell
cd pipeline
python run.py "https://www.youtube.com/watch?v=VIDEO_ID" --full
```

## 输出（与仓库其它模块约定一致）

| 路径 | 说明 |
|------|------|
| `output/raw/<id>/<id>.mp4` | 原片 |
| `output/processed/<id>/<id>_zh_dub_hard_bilingual.mp4` | 配音成片（来自 VideoLingo `output/output_dub.mp4`） |
| `output/processed/<id>/<id>.bilingual.srt` | 双语字幕 |

## UI（可选）

```powershell
cd F:\project\youtube-vid-translate
.\.venv\Scripts\Activate.ps1
cd pipeline
python -m streamlit run st.py
```

你已在 `pipeline` 目录时，不要再 `cd pipeline`；用 `python -m streamlit` 而不是直接敲 `streamlit`。

## 迁移说明

原 `scripts/`、`vidtranslate/` 已移至 `_legacy_backup/`（可删除）。适配逻辑见 `bridge/runner.py`、`bridge/export_layout.py`。
