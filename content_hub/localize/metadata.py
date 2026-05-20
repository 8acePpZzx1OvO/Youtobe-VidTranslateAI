"""发布元数据本地化（标题/简介模板填充）。"""

from __future__ import annotations

from typing import Any


def build_publish_metadata(
    *,
    title: str,
    source_url: str,
    channel: str,
    rules: dict[str, Any],
) -> dict[str, Any]:
    tpl = rules.get("title_template") or "{title}"
    localized_title = tpl.format(title=title, source_url=source_url, channel=channel)

    footer = (rules.get("description_footer") or "").format(
        title=title,
        source_url=source_url,
        channel=channel,
    )
    description = footer.strip()

    tags = list(rules.get("tags") or [])
    return {
        "title": localized_title,
        "description": description,
        "tags": tags,
        "source_url": source_url,
        "channel": channel,
    }
