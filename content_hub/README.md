# content_hub — 外文视频搬运分发流

在 **video_fetcher**（发现/下载）与 **pipeline**（译配成片）之上，编排「按类型筛选 → 全自动译配 → B站 / 微信视频号发布」。

## 合规

- 仅搬运**已获授权**或平台允许转载的内容。
- 简介须含**原链接、原频道**；标注 **AI 配音译制**。
- 遵守 [YouTube 服务条款](https://www.youtube.com/static?template=terms)、B站与微信运营规范。

## 安装

```powershell
cd F:\project\youtube-vid-translate
pip install -e ".[content-hub,pipeline]"
pip install -r content_hub/requirements-publish.txt
```

## 配置

```powershell
copy content_hub\.env.example content_hub\.env
copy content_hub\config\sources.example.yaml content_hub\config\sources.yaml
# 编辑 sources.yaml、filters.yaml、publish_rules.yaml
```

默认 **`CONTENT_HUB_PUBLISH_DRY_RUN=1`**：发布步骤只写台账，不实际上传。

## 命令

```powershell
# 上手自检（翻译 Key、代理是否可达、可选检查成片）
content-hub doctor
content-hub doctor --video-id rFG-Sx-Tz6o

# 全流程（发现 → workflow 译配 → publish_ready → 发布）
content-hub run-once --config content_hub/config/sources.yaml

# 仅发现，写入 content_hub/data/jobs.db
content-hub discover --config content_hub/config/sources.yaml

# 仅发布台账中 publish_ready 的任务
content-hub publish-ready --config content_hub/config/sources.yaml

# 定时轮询（或交给系统计划任务）
content-hub daemon --config content_hub/config/sources.yaml --interval 3600

# 单条译制（需先 content-hub doctor 通过；会调用 pipeline/run.py --full）
.\content_hub\scripts\translate_one.ps1 rFG-Sx-Tz6o
```

**注意：** `pipeline/.env` 里若配置了 `YOUTOBE_YTDLP_PROXY`，须先启动本机代理（如 `127.0.0.1:13434`），否则下载会失败。

## 目录

| 路径 | 说明 |
|------|------|
| `content_hub/config/` | sources / filters / publish_rules / platforms |
| `content_hub/data/jobs.db` | 任务台账（SQLite） |
| `pipeline/output/publish_ready/<id>/` | manifest.json + video.mp4 + subtitles.srt |

架构详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 与 video_fetcher 的关系

| 命令 | 场景 |
|------|------|
| `video-fetcher workflow` | 只译配归档，不发布 |
| `content-hub run-once` | 译配 + 元数据 + 多平台发布 |
