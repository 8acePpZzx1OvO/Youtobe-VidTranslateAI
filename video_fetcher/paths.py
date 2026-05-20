"""

【模块】video_fetcher.paths — 解析仓库根、pipeline 根与 raw/processed 输出路径。

【调用方】cli、batch、pipeline_runner；与 vidtranslate.layout 的嵌套目录约定一致。

"""



from __future__ import annotations

import sys
from pathlib import Path



# 新目录名优先；保留旧名便于迁移期定位 run.py

_PIPELINE_DIRNAMES = ("pipeline", "youtobe pipeline")





def find_repo_root(start: Path | None = None) -> Path:

    """自 video_fetcher 包或 cwd 向上查找含 pipeline/run.py 的仓库根。"""

    if start is None:

        start = Path.cwd()

    start = start.resolve()

    candidates = [start, *start.parents]

    pkg_anchor = Path(__file__).resolve().parent.parent

    if pkg_anchor not in candidates:

        candidates.insert(0, pkg_anchor)

    for base in candidates:

        for name in _PIPELINE_DIRNAMES:

            if (base / name / "run.py").is_file():

                return base

    raise FileNotFoundError(

        f"未找到仓库根（需存在 pipeline/run.py），"

        f"请从 youtube-vid-translate 仓库内运行或设置正确工作目录。"

    )





def pipeline_root(repo: Path | None = None) -> Path:

    root = repo or find_repo_root()

    for name in _PIPELINE_DIRNAMES:

        p = root / name

        if (p / "run.py").is_file():

            return p

    return root / _PIPELINE_DIRNAMES[0]


def resolve_python_executable(repo: Path | None = None) -> str:
    """
    优先使用仓库 .venv 中的 Python，避免系统 python 缺 VideoLingo 依赖。
    可通过环境变量 YOUTUBE_VID_TRANSLATE_PYTHON 覆盖。
    """
    import os

    override = os.environ.get("YOUTUBE_VID_TRANSLATE_PYTHON", "").strip()
    if override:
        return override

    root = repo or find_repo_root()
    if sys.platform == "win32":
        candidates = [
            root / ".venv" / "Scripts" / "python.exe",
            root / "venv" / "Scripts" / "python.exe",
        ]
    else:
        candidates = [
            root / ".venv" / "bin" / "python",
            root / "venv" / "bin" / "python",
        ]
    for p in candidates:
        if p.is_file():
            return str(p.resolve())
    return sys.executable





def output_raw_root(pipeline: Path | None = None) -> Path:

    return (pipeline or pipeline_root()) / "output" / "raw"





def output_processed_root(pipeline: Path | None = None) -> Path:

    return (pipeline or pipeline_root()) / "output" / "processed"





def raw_video_dir(raw_root: Path, video_id: str) -> Path:

    """raw/<video_id>/ 目录（与 vidtranslate.layout.raw_nested_dir 一致）。"""

    return raw_root / video_id





def raw_mp4_path(raw_root: Path, video_id: str) -> Path:

    return raw_video_dir(raw_root, video_id) / f"{video_id}.mp4"





def raw_vtt_path(raw_root: Path, video_id: str) -> Path:

    return raw_video_dir(raw_root, video_id) / f"{video_id}.en.vtt"





def has_raw_download(raw_root: Path, video_id: str) -> bool:

    """已存在原片 mp4 视为可跳过下载。"""

    return raw_mp4_path(raw_root, video_id).is_file()

