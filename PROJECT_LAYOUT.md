# 仓库目录索引

> 新开 Cursor 会话时，**先读** [`pipeline/docs/HANDOFF_新对话快速上手.md`](pipeline/docs/HANDOFF_新对话快速上手.md)，再查 [`pipeline/docs/PROJECT_HANDOFF.md`](pipeline/docs/PROJECT_HANDOFF.md) 的最近变更。

## 顶层结构

```
youtube-vid-translate/          # 建议克隆目录名（原 Youtobe-demo 可改名）
├── README.md
├── PROJECT_LAYOUT.md           # 本文件
├── pyproject.toml              # youtube-vid-translate：video_fetcher + vidtranslate
├── examples/channel_lists/     # 批量 URL 列表
├── examples/content_hub/       # content_hub 示例 sources 配置
├── video_fetcher/              # 批量下载 + workflow
├── content_hub/                # 发现 → 译配 → B站/视频号发布编排
├── pipeline/                   # VideoLingo + bridge 适配（cd 到此执行 run.py）
│   ├── run.py                  # CLI 入口
│   ├── bridge/                 # 与 raw/processed 输出布局桥接
│   ├── core/                   # VideoLingo 核心
│   ├── config.yaml
│   ├── output/
│   └── st.py                   # Streamlit UI（可选）
└── vendor/
```

> **迁移**：若仍存在旧目录 `youtobe pipeline/`（含历史 output），在关闭占用进程后可删除；`video_fetcher.paths` 会优先使用 `pipeline/`。

## 三大模块

| 模块 | 入口 | 职责 |
|------|------|------|
| **video_fetcher** | `video-fetcher` / `python -m video_fetcher` | 频道/列表批量下载；`workflow` 调 `pipeline/run.py --full` |
| **pipeline** | `python run.py` | VideoLingo：WhisperX → 翻译 → 配音 → 成片 |
| **content_hub** | `content-hub` / `python -m content_hub` | 按类型发现 → 台账 → 译配桥接 → `publish_ready` → B站/视频号发布 |

`content_hub` 任务库默认：`content_hub/data/jobs.db`。发布包：`pipeline/output/publish_ready/<视频ID>/`。

## VideoLingo（`pipeline/`）

上游：[Huanshere/VideoLingo](https://github.com/Huanshere/VideoLingo)。仓库通过 `pipeline/bridge/` 将成片导出到 `output/raw|processed/<视频ID>/`。

环境变量：`pipeline/.env`（`DEEPSEEK_*`、`YOUTOBE_YTDLP_PROXY` 等）会在 `run.py` 启动时同步到 `config.yaml` 的 `api` 段。

## 维护约定

1. VideoLingo 本体在 `pipeline/core/`；仓库定制只改 `pipeline/bridge/` 与 `run.py`。
2. 升级 VideoLingo：对比上游 release，合并 `core/`、`config.yaml`，保留 `bridge/`。
3. 功能改动 → 更新 `pipeline/README.md` 与根 `README.md`。

## 相关文档

| 文档 | 说明 |
|------|------|
| [VideoLingo 文档](https://docs.videolingo.io) | 上游功能与 API |
| [pipeline README](pipeline/README.md) | 本仓库安装与命令 |
| [video_fetcher](video_fetcher/README.md) | 批量 CLI |
| [content_hub](content_hub/README.md) | 搬运分发流 |
| [content_hub 架构](content_hub/docs/ARCHITECTURE.md) | 状态机与平台适配 |
