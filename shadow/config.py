"""Configuration loading.

One YAML file drives every stage. Nothing else in the package reads
environment-specific paths or URLs directly.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent


class AppConfig(BaseModel):
    base_url: str
    site: str
    username: str
    password: str
    host_header: str | None = None


class ProxyConfig(BaseModel):
    port: int = 8081
    listen_host: str = "127.0.0.1"
    ca_dir: str = "~/.mitmproxy"
    mitmdump: str = ".venv-capture/bin/mitmdump"


class BrowserConfig(BaseModel):
    executable_path: str = ""
    headless: bool = True
    slow_mo_ms: int = 0
    viewport: tuple[int, int] = (1440, 900)


class PathsConfig(BaseModel):
    artifacts: str
    capture: str
    filtered: str
    episodes: str
    tools: str
    mcp_server: str
    results: str
    charts: str
    split: str
    credentials: str

    def resolve(self, key: str) -> Path:
        p = Path(getattr(self, key))
        return p if p.is_absolute() else REPO_ROOT / p


class ModelCost(BaseModel):
    input: float
    cached_input: float
    output: float


class ModelsConfig(BaseModel):
    agent: str
    labeler: str
    namer: str
    provider: str = "auto"


class SegmentConfig(BaseModel):
    idle_gap_s: float = 8.0
    min_records: int = 2
    merge_gap_s: float = 20.0
    coherence_threshold: float = 0.5


class ProvenanceConfig(BaseModel):
    min_value_len: int = 3
    stoplist: list[str] = Field(default_factory=list)
    max_lookback_steps: int = 40


class InduceConfig(BaseModel):
    min_support: int = 3
    enum_max: int = 8
    max_tools: int = 25
    required_presence: float = 0.9
    chrome_df: float = 0.8


class BenchConfig(BaseModel):
    split_seed: int = 1337
    observe_fraction: float = 0.6
    trials: int = 3
    max_steps: int = 25
    task_timeout_s: int = 240


class VerifyConfig(BaseModel):
    allow_writes: bool = False


class Config(BaseModel):
    app: AppConfig
    proxy: ProxyConfig
    browser: BrowserConfig = BrowserConfig()
    paths: PathsConfig
    models: ModelsConfig
    costs: dict[str, ModelCost]
    segment: SegmentConfig
    provenance: ProvenanceConfig
    induce: InduceConfig
    bench: BenchConfig
    verify: VerifyConfig

    def path(self, key: str) -> Path:
        return self.paths.resolve(key)


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    cfg_path = Path(path) if path else REPO_ROOT / "config.yaml"
    raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text())
    return Config.model_validate(raw)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config(os.environ.get("SHADOW_CONFIG"))
