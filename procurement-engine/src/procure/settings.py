"""Runtime settings (from environment / .env) and YAML configuration loading."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    """Environment-driven settings. Read once per process (see get_settings)."""

    def __init__(self) -> None:
        self.project_root = PROJECT_ROOT
        self.data_dir = Path(os.getenv("PROCURE_DATA_DIR", PROJECT_ROOT / "data"))
        self.raw_dir = self.data_dir / "raw"
        self.db_path = Path(os.getenv("PROCURE_DB_PATH", self.data_dir / "app.duckdb"))
        self.config_dir = Path(os.getenv("PROCURE_CONFIG_DIR", PROJECT_ROOT / "config"))
        self.offline = os.getenv("OFFLINE", "0").strip().lower() in {"1", "true", "yes"}
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip() or None
        self.seed = int(os.getenv("PROCURE_SEED", "0") or 0) or None
        self.http_timeout = float(os.getenv("PROCURE_HTTP_TIMEOUT", "15"))
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    @property
    def sample_news_path(self) -> Path:
        """Bundled sample headlines ship with the code, not the data dir."""
        return PROJECT_ROOT / "data" / "raw" / "sample_news.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def _load_yaml(name: str) -> dict[str, Any]:
    path = get_settings().config_dir / name
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def materials_config() -> dict[str, Any]:
    return _load_yaml("materials.yaml")


@lru_cache(maxsize=1)
def assumptions() -> dict[str, Any]:
    cfg = _load_yaml("assumptions.yaml")
    env_seed = get_settings().seed
    if env_seed is not None:
        cfg["simulation"]["seed"] = env_seed
    return cfg


def material_keys() -> list[str]:
    return list(materials_config()["materials"].keys())


def material_cfg(material: str) -> dict[str, Any]:
    mats = materials_config()["materials"]
    if material not in mats:
        raise KeyError(f"Unknown material '{material}'. Known: {', '.join(mats)}")
    return mats[material]


def reset_caches() -> None:
    """Used by tests after changing environment variables."""
    get_settings.cache_clear()
    materials_config.cache_clear()
    assumptions.cache_clear()
