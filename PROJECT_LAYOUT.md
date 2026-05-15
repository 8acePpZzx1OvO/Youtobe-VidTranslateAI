# 仓库目录索引

> 新开 Cursor 会话时，**先读** [`youtobe pipeline/docs/PROJECT_HANDOFF.md`](youtobe%20pipeline/docs/PROJECT_HANDOFF.md)（结构、流程、最近变更）。  
> 历史对话详录见 [`youtobe pipeline/docs/SESSION_HANDOFF_对话与任务全记录.md`](youtobe%20pipeline/docs/SESSION_HANDOFF_对话与任务全记录.md)。

## 顶层

| 路径 | 作用 |
|------|------|
| `youtobe pipeline/` | YouTube 译配主流水线（`run.py` 入口） |
| `video_fetcher/` | 频道/列表批量下载并调用 pipeline |
| `vendor/` | 第三方克隆（如 fish-speech，gitignore） |

## 译配流水线（`youtobe pipeline/`）

| 路径 | 作用 |
|------|------|
| `run.py` | 一键编排 |
| `scripts/` | 下载、翻译、配音、封装、烧录等步骤脚本 |
| `config/` | 默认 env、主题预设 YAML |
| `output/raw/` | 原视频 `{id}/{id}.mp4` |
| `output/processed/` | 字幕、配音、成片 `{id}/` |
| `env.example` | 环境变量模板 |
| `docs/PROJECT_HANDOFF.md` | **Living 交接文档（每次功能改动必更新）** |

## 维护约定

每次功能优化或新增后，更新 `docs/PROJECT_HANDOFF.md` 的「最近变更」及相关章节。详见 `.cursor/rules/project-handoff.mdc`。
