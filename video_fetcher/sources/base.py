"""
【模块】video_fetcher.sources.base — 视频源抽象接口：解析 URL、下载、字幕元数据（占位）。
【调用方】各具体站点模块继承并实现 fetch() / resolve_id() 等。

设计目标：与 yt-dlp 解耦或封装 yt-dlp，便于单测与多源扩展。
"""
