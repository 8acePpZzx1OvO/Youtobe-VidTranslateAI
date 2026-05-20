"""加载 content_hub YAML 配置（需 PyYAML，见 optional-dependencies content-hub）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from content_hub.paths import content_hub_root


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as e:
        raise ImportError(
            "加载 YAML 需要 PyYAML：pip install -e \".[content-hub]\""
        ) from e
    if not path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def resolve_config_path(path: str | Path, *, base: Path | None = None) -> Path:
    p = Path(path)
    if p.is_file():
        return p.resolve()
    root = base or content_hub_root()
    candidate = root / p
    if candidate.is_file():
        return candidate.resolve()
    repo = root.parent if root.name == "content_hub" else root
    for anchor in (repo, Path.cwd()):
        c = anchor / p
        if c.is_file():
            return c.resolve()
    return p.resolve()


def load_sources_config(path: str | Path) -> dict[str, Any]:
    cfg_path = resolve_config_path(path)
    cfg = _load_yaml(cfg_path)
    cfg["_config_path"] = str(cfg_path)
    return cfg


def load_filters_config(sources_cfg: dict[str, Any]) -> dict[str, Any]:
    rel = sources_cfg.get("filters") or "config/filters.yaml"
    base = Path(sources_cfg["_config_path"]).parent
    if Path(rel).is_absolute():
        return _load_yaml(Path(rel))
    hub = content_hub_root()
    for candidate in (base / rel, hub / rel, hub / "config" / Path(rel).name):
        if candidate.is_file():
            return _load_yaml(candidate)
    example = hub / "config" / "filters.example.yaml"
    if example.is_file():
        return _load_yaml(example)
    return {}


def load_publish_rules(sources_cfg: dict[str, Any]) -> dict[str, Any]:
    rel = sources_cfg.get("publish_rules") or "config/publish_rules.yaml"
    base = Path(sources_cfg["_config_path"]).parent
    hub = content_hub_root()
    for candidate in (base / rel, hub / rel, hub / "config" / Path(rel).name):
        if candidate.is_file():
            return _load_yaml(candidate)
    example = hub / "config" / "publish_rules.example.yaml"
    if example.is_file():
        return _load_yaml(example)
    return {}


def load_platforms_config(sources_cfg: dict[str, Any]) -> dict[str, Any]:
    rel = sources_cfg.get("platforms") or "config/platforms.yaml"
    base = Path(sources_cfg["_config_path"]).parent
    hub = content_hub_root()
    for candidate in (base / rel, hub / rel, hub / "config" / Path(rel).name):
        if candidate.is_file():
            return _load_yaml(candidate)
    example = hub / "config" / "platforms.example.yaml"
    if example.is_file():
        return _load_yaml(example)
    return {}
