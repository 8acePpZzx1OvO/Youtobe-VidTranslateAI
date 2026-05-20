"""上手环境自检：配置、代理、台账、成片路径。"""

from __future__ import annotations

import os
import socket
import sys
import urllib.parse
from pathlib import Path

from video_fetcher.paths import pipeline_root

from content_hub.paths import catalog_db_path, content_hub_root, find_hard_burn_mp4
from content_hub.paths import output_processed_root


def _load_pipeline_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(pipeline_root() / ".env", override=False)
    load_dotenv(content_hub_root() / ".env", override=False)


def _translation_key_ok() -> tuple[bool, str]:
    _load_pipeline_env()
    keys = (
        "DEEPL_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "YOUTOBE_LLM_API_KEY",
        "MICROSOFT_API_KEY",
    )
    for k in keys:
        if os.getenv(k, "").strip():
            return True, k
    return False, "未配置翻译 Key（pipeline/.env）"


def _proxy_reachable() -> tuple[bool, str]:
    _load_pipeline_env()
    proxy = (
        os.getenv("YOUTOBE_YTDLP_PROXY", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
    )
    if not proxy:
        return True, "未配置代理（若本机可直接访问 YouTube 可忽略）"
    parsed = urllib.parse.urlparse(proxy)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return True, f"代理可达 {host}:{port}"
    except OSError as e:
        return False, f"代理不可达 {host}:{port}（请先启动科学上网客户端）: {e}"


def run_doctor(*, video_id: str | None = None) -> int:
    ok = True
    print("=== content_hub 环境自检 ===\n")

    hub = content_hub_root()
    for name, path in (
        ("content_hub/.env", hub / ".env"),
        ("content_hub/config/sources.yaml", hub / "config" / "sources.yaml"),
        ("pipeline/.env", pipeline_root() / ".env"),
    ):
        exists = path.is_file()
        print(f"[{'OK' if exists else 'MISS'}] {name}")
        if not exists and "sources" in name:
            print("       提示: copy content_hub\\config\\sources.example.yaml sources.yaml")
        ok = ok and (exists or "sources" not in name)

    tr_ok, tr_msg = _translation_key_ok()
    print(f"[{'OK' if tr_ok else 'FAIL'}] 翻译 API: {tr_msg}")
    ok = ok and tr_ok

    px_ok, px_msg = _proxy_reachable()
    print(f"[{'OK' if px_ok else 'WARN'}] {px_msg}")
    if not px_ok:
        ok = False

    db = catalog_db_path()
    print(f"[{'OK' if db.is_file() else 'INFO'}] 台账: {db}")

    if video_id:
        proc = output_processed_root()
        hard = find_hard_burn_mp4(proc, video_id)
        if hard and hard.is_file():
            size_mb = hard.stat().st_size / (1024 * 1024)
            print(f"[OK] 成片: {hard} ({size_mb:.1f} MB)")
        else:
            print(f"[MISS] 成片: processed/{video_id}/*_zh_dub_hard_bilingual.mp4")
            ok = False

    print()
    if ok:
        print("自检通过。下一步译制单条视频：")
        print('  cd pipeline')
        print('  python run.py "https://www.youtube.com/watch?v=VIDEO_ID" --full')
    else:
        print("请先修复 FAIL/WARN 项后再跑 run.py --full。")
    return 0 if ok else 1


if __name__ == "__main__":
    vid = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(run_doctor(video_id=vid))
