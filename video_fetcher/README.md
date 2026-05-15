# video_fetcher

批量拉取列表中的视频 URL 时，请与根目录 **`youtobe pipeline/run.py --full`** 的输出约定对齐：

- **`output/raw/<视频ID>/`**：流水线成功且未加 `--keep-intermediate` 时，仅保留 **`<视频ID>.mp4`**（原片）。
- **`output/processed/<视频ID>/`**：仅保留 **`<视频ID>.en.srt`**、**`<视频ID>.zh.srt`**、**`<视频ID>_zh_dub_hard_bilingual.mp4`**（及可选倍速副本 `*_x…*.mp4`）。

需要保留 VTT、双语 SRT、软字幕 MP4、单独配音轨时，运行 `run.py` / `finish_outputs.py` 时加上 **`--keep-intermediate`**，或设置 **`YOUTOBE_MINIMIZE_OUTPUTS=0`**。
