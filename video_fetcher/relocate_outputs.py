"""
【模块】video_fetcher.relocate_outputs — 搬运归档布局：raw 仅 mp4，processed 仅双语 SRT + 硬烧配音成片。
【调用方】workflow / pipeline --relocate；在 run.py --keep-intermediate 完成后调用。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from video_fetcher.paths import pipeline_root, raw_mp4_path

logger = logging.getLogger(__name__)


def _import_minimize_raw():
    pipe = pipeline_root()
    pipe_s = str(pipe.resolve())
    if pipe_s not in sys.path:
        sys.path.insert(0, pipe_s)
    from bridge.export_layout import minimize_raw_dir  # noqa: WPS433

    return minimize_raw_dir


def _unlink_any(path: Path) -> None:
    import shutil

    try:
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def ensure_bilingual_srt(proc_base: Path, stem: str) -> Path | None:
    """若仅有 en/zh SRT 则调用 merge_bilingual_srt 生成 .bilingual.srt。"""
    bi = proc_base / f"{stem}.bilingual.srt"
    if bi.is_file():
        return bi
    en = proc_base / f"{stem}.en.srt"
    zh = proc_base / f"{stem}.zh.srt"
    if not en.is_file() or not zh.is_file():
        return bi if bi.is_file() else None
    if bi.is_file():
        return bi
    if en.is_file() and zh.is_file():
        try:
            bi.write_text(
                "\n".join(
                    [
                        _merge_bilingual_content(en.read_text(encoding="utf-8"), zh.read_text(encoding="utf-8"))
                    ]
                ),
                encoding="utf-8",
            )
            return bi if bi.is_file() else None
        except OSError:
            logger.warning("无法合并双语 SRT stem=%s", stem)
    return None


def _merge_bilingual_content(en_srt: str, zh_srt: str) -> str:
    """简易双语 SRT：按块拼接英文与中文行（VideoLingo 通常已提供 src_trans.srt）。"""
    return f"{en_srt.strip()}\n\n{zh_srt.strip()}"


def minimize_proc_relocate(proc_base: Path, stem: str) -> list[Path]:
    """
    processed/<stem>/ 仅保留：
      <stem>.bilingual.srt
      <stem>_zh_dub_hard_bilingual*.mp4
    返回保留文件路径列表。
    """
    if not proc_base.is_dir():
        return []
    ensure_bilingual_srt(proc_base, stem)
    kept: list[Path] = []
    for p in list(proc_base.iterdir()):
        if p.is_dir():
            _unlink_any(p)
            continue
        if not p.is_file():
            continue
        n = p.name
        ok = n == f"{stem}.bilingual.srt" or (
            n.startswith(f"{stem}_zh_dub_hard_bilingual") and n.endswith(".mp4")
        )
        if ok:
            kept.append(p)
        else:
            _unlink_any(p)
    return kept


def apply_relocate_layout(
    raw_root: Path,
    proc_root: Path,
    video_id: str,
) -> dict:
    """
    应用搬运目录约定。
    返回 {"raw_mp4", "bilingual_srt", "hard_mp4", "kept": [...]}。
    """
    stem = video_id.strip()
    minimize_raw = _import_minimize_raw()
    minimize_raw(raw_root, stem)

    proc_base = proc_root / stem
    kept = minimize_proc_relocate(proc_base, stem)

    mp4 = raw_mp4_path(raw_root, stem)
    bi = proc_base / f"{stem}.bilingual.srt"
    hard = next(
        (
            p
            for p in kept
            if p.name.startswith(f"{stem}_zh_dub_hard_bilingual")
            and p.suffix == ".mp4"
        ),
        None,
    )
    out = {
        "video_id": stem,
        "raw_mp4": str(mp4) if mp4.is_file() else None,
        "bilingual_srt": str(bi) if bi.is_file() else None,
        "hard_mp4": str(hard) if hard else None,
        "kept": [str(p) for p in kept],
    }
    logger.info(
        "已归档 %s: raw=%s bilingual=%s hard=%s",
        stem,
        out["raw_mp4"],
        out["bilingual_srt"],
        out["hard_mp4"],
    )
    return out
