"""将 VideoLingo output/ 导出为 raw/processed 兼容目录。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

PIPE = Path(__file__).resolve().parent.parent
VL_OUTPUT = PIPE / "output"


def minimize_raw_dir(raw_root: Path, stem: str) -> None:
    d = raw_root / stem
    if not d.is_dir():
        return
    keep = d / f"{stem}.mp4"
    for p in list(d.iterdir()):
        if p.is_file() and p != keep:
            p.unlink(missing_ok=True)
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def export_to_repo_layout(
    video_id: str,
    *,
    raw_root: Path,
    proc_root: Path,
    prefer_dub: bool = True,
) -> dict:
    stem = video_id.strip()
    raw_dir = raw_root / stem
    proc_dir = proc_root / stem
    raw_dir.mkdir(parents=True, exist_ok=True)
    proc_dir.mkdir(parents=True, exist_ok=True)

    src_mp4 = _find_source_mp4()
    if not src_mp4:
        raise FileNotFoundError("VideoLingo output 中未找到源视频 mp4")

    dest_raw = raw_dir / f"{stem}.mp4"
    if src_mp4.suffix.lower() != ".mp4":
        import subprocess

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src_mp4),
                "-c",
                "copy",
                str(dest_raw),
            ],
            check=False,
            capture_output=True,
        )
        if not dest_raw.is_file():
            shutil.copy2(src_mp4, dest_raw.with_suffix(src_mp4.suffix))
            dest_raw = dest_raw.with_suffix(src_mp4.suffix)
    else:
        shutil.copy2(src_mp4, dest_raw)

    hard_src = None
    if prefer_dub and (VL_OUTPUT / "output_dub.mp4").is_file():
        hard_src = VL_OUTPUT / "output_dub.mp4"
    elif (VL_OUTPUT / "output_sub.mp4").is_file():
        hard_src = VL_OUTPUT / "output_sub.mp4"

    hard_dest = proc_dir / f"{stem}_zh_dub_hard_bilingual.mp4"
    if hard_src:
        shutil.copy2(hard_src, hard_dest)

    bi_src = _pick_bilingual_srt()
    bi_dest = proc_dir / f"{stem}.bilingual.srt"
    if bi_src:
        shutil.copy2(bi_src, bi_dest)

    trans = VL_OUTPUT / "trans.srt"
    src = VL_OUTPUT / "src.srt"
    if trans.is_file():
        shutil.copy2(trans, proc_dir / f"{stem}.zh.srt")
    if src.is_file():
        shutil.copy2(src, proc_dir / f"{stem}.en.srt")

    return {
        "video_id": stem,
        "raw_mp4": str(dest_raw),
        "hard_mp4": str(hard_dest) if hard_dest.is_file() else None,
        "bilingual_srt": str(bi_dest) if bi_dest.is_file() else None,
    }


def _find_source_mp4() -> Path | None:
    if not VL_OUTPUT.is_dir():
        return None
    skip = {"output_sub.mp4", "output_dub.mp4", "black_screen.mp4"}
    video_ext = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv", ".wmv"}
    cands = [
        p
        for p in VL_OUTPUT.iterdir()
        if p.is_file()
        and p.suffix.lower() in video_ext
        and p.name not in skip
    ]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_size)


def _pick_bilingual_srt() -> Path | None:
    for name in ("src_trans.srt", "trans_src.srt", "trans.srt"):
        p = VL_OUTPUT / name
        if p.is_file():
            return p
    return None
