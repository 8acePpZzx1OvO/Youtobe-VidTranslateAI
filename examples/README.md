# 示例与批处理输入

| 路径 | 用途 |
|------|------|
| `channel_lists/*.txt` | 每行一个 YouTube URL，供 `video-fetcher batch` / `workflow` 使用 |
| `content_hub/` | `content-hub` 示例 `sources/*.yaml` |

示例：

```powershell
video-fetcher batch examples/channel_lists/laughoverlife_latest5.txt --resume
```
