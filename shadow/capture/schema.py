"""Typed artifacts shared by every stage of the pipeline.

Everything that crosses a module boundary is one of these models, so the
capture -> distill -> serve -> bench chain is checkable end to end.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field

Transform = Literal["identity", "str", "int", "float", "urlencode", "lower", "json"]
BindingKind = Literal["literal", "from_response", "user_param"]
MutationClass = Literal["read", "write", "destructive"]


class HttpRecord(BaseModel):
    """One request/response pair observed on the wire."""

    id: str
    episode_id: str | None = None
    ts_start: float
    ts_end: float
    method: str
    url: str
    path: str
    query: dict[str, Any] = Field(default_factory=dict)
    req_headers: dict[str, str] = Field(default_factory=dict)
    req_body: Any | None = None
    status: int = 0
    resp_headers: dict[str, str] = Field(default_factory=dict)
    resp_body: Any | None = None
    duration_ms: float = 0.0

    # Filter annotations (set by distill.filter, never by the capture addon).
    is_polling: bool = False
    is_document: bool = False
    content_type: str = ""
    # Number of raw flows this record stands in for after polling collapse.
    collapsed_count: int = 1

    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000.0


class Episode(BaseModel):
    """A contiguous run of records believed to be one user-level task."""

    id: str
    template_id: str | None = None
    records: list[HttpRecord] = Field(default_factory=list)
    label: str | None = None
    coherence: float | None = None

    @property
    def ts_start(self) -> float:
        return self.records[0].ts_start if self.records else 0.0

    @property
    def ts_end(self) -> float:
        return self.records[-1].ts_end if self.records else 0.0


class Binding(BaseModel):
    """How one parameter value is filled at execution time."""

    kind: BindingKind
    literal_value: Any | None = None
    source_step_index: int | None = None
    json_path: str | None = None
    transform: Transform = "identity"
    param_name: str | None = None
    # Diagnostics: kept so a provenance trace is auditable after the fact.
    example_value: Any | None = None
    confidence: float = 1.0

    def describe(self) -> str:
        if self.kind == "literal":
            return f"literal({self.literal_value!r})"
        if self.kind == "user_param":
            return f"param({self.param_name})"
        return f"step[{self.source_step_index}]{self.json_path} via {self.transform}"


class ToolStep(BaseModel):
    method: str
    path_template: str
    query_bindings: dict[str, Binding] = Field(default_factory=dict)
    body_bindings: dict[str, Binding] = Field(default_factory=dict)
    header_bindings: dict[str, Binding] = Field(default_factory=dict)
    # Path segments may themselves be bound (e.g. /api/resource/Customer/{p0}).
    path_bindings: dict[str, Binding] = Field(default_factory=dict)
    # "form" for application/x-www-form-urlencoded, "json" otherwise.
    body_encoding: Literal["json", "form", "none"] = "none"


class ToolSpec(BaseModel):
    name: str
    description: str
    params_schema: dict[str, Any] = Field(default_factory=dict)
    steps: list[ToolStep] = Field(default_factory=list)
    mutation_class: MutationClass = "read"
    support: int = 0
    verified: bool = False
    # Provenance back to the observation set, for auditing.
    source_episode_ids: list[str] = Field(default_factory=list)
    signature: str = ""
    verify_note: str = ""
    # Shape of the final step's observed response: json-path -> type name.
    # Used by verify/replay.py to check that a replay still looks right
    # without asserting on values, which change between runs.
    response_shape: dict[str, str] = Field(default_factory=dict)
    # Example arguments taken from one observed episode, for replay.
    example_args: dict[str, Any] = Field(default_factory=dict)
    # Diagnostics for the provenance false-positive metric.
    n_from_response_bindings: int = 0


class ToolCatalog(BaseModel):
    tools: list[ToolSpec] = Field(default_factory=list)
    generated_from_episodes: int = 0
    observe_template_ids: list[str] = Field(default_factory=list)

    def by_name(self, name: str) -> ToolSpec | None:
        return next((t for t in self.tools if t.name == name), None)


# --------------------------------------------------------------------------
# JSONL helpers. Capture files get large; everything streams.
# --------------------------------------------------------------------------

def write_jsonl(path: str | Path, records: list[BaseModel]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as fh:
        for r in records:
            fh.write(r.model_dump_json() + "\n")


def append_jsonl(path: str | Path, record: BaseModel) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(record.model_dump_json() + "\n")


def read_jsonl(path: str | Path, model: type[BaseModel]) -> Iterator[Any]:
    p = Path(path)
    if not p.exists():
        return
    with p.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield model.model_validate_json(line)


def read_records(path: str | Path) -> list[HttpRecord]:
    return list(read_jsonl(path, HttpRecord))


def write_episodes(path: str | Path, episodes: list[Episode]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([e.model_dump() for e in episodes], indent=2))


def read_episodes(path: str | Path) -> list[Episode]:
    p = Path(path)
    if not p.exists():
        return []
    return [Episode.model_validate(d) for d in json.loads(p.read_text())]


def write_catalog(path: str | Path, catalog: ToolCatalog) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(catalog.model_dump_json(indent=2))


def read_catalog(path: str | Path) -> ToolCatalog:
    p = Path(path)
    if not p.exists():
        return ToolCatalog()
    return ToolCatalog.model_validate_json(p.read_text())
