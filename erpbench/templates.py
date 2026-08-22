"""WorkflowTemplate — SPEC §4.

A template *generates* instances together with their assertions and their
mutation envelope. Hand-writing assertions per instance is what caps a
dataset at a few dozen tasks and kills the scaling study, so nothing here
takes a hand-written assertion.

The parameter axes are **structural**. Renaming a customer produces another
instance of the same problem; moving an amount from below a threshold to
above it produces a different problem with a different correct answer. Only
the second kind is worth sampling, and the axes below are the ones that
change what the right outcome *is*.

Sampling is deliberately non-uniform. SPEC §4 asks for failure clusters,
child-table work, threshold boundaries, ambiguous matches, and cases where
the correct outcome is escalation or no write at all -- uniform sampling
spends the budget on easy instances and flattens the curve that the whole
scaling study depends on.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator

from erpbench.firms import Firm
from erpbench.verify import Assertion, MutationEnvelope


class EntityPresence(str, Enum):
    EXISTS = "exists"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"        # two near-matches; picking either is wrong


class AmountBand(str, Enum):
    BELOW = "below"
    AT = "at"                      # exactly on the threshold -- inclusive by policy
    ABOVE = "above"


class Evidence(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    STALE = "stale"                # exists but does not cover this document


class SideEffect(str, Enum):
    NONE = "none"
    ONE_REQUIRED = "one_required"
    ONE_FORBIDDEN = "one_forbidden"


class Information(str, Enum):
    COMPLETE = "complete"
    ONE_MISSING = "one_missing"
    CONTRADICTORY = "contradictory"


@dataclass(frozen=True)
class Params:
    """One point in the structural space, plus the concrete values drawn
    for it. `seed` makes the draw reproducible and is part of the run_id."""
    seed: int
    entity: EntityPresence = EntityPresence.EXISTS
    amount_band: AmountBand = AmountBand.BELOW
    evidence: Evidence = Evidence.PRESENT
    side_effect: SideEffect = SideEffect.NONE
    information: Information = Information.COMPLETE
    scale: int = 1
    values: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def axes(self) -> dict[str, Any]:
        return {"entity": self.entity.value, "amount_band": self.amount_band.value,
                "evidence": self.evidence.value, "side_effect": self.side_effect.value,
                "information": self.information.value, "scale": self.scale}


# Weights, not a uniform grid. The rare-and-hard cases are the ones that
# separate a trained model from a base one; sampling them at 1/3 each would
# fill the corpus with the easy corner.
DEFAULT_WEIGHTS: dict[str, dict[Any, float]] = {
    "entity":      {EntityPresence.EXISTS: 0.45, EntityPresence.MISSING: 0.35,
                    EntityPresence.AMBIGUOUS: 0.20},
    "amount_band": {AmountBand.BELOW: 0.35, AmountBand.AT: 0.25,
                    AmountBand.ABOVE: 0.40},
    "evidence":    {Evidence.PRESENT: 0.45, Evidence.ABSENT: 0.35,
                    Evidence.STALE: 0.20},
    "side_effect": {SideEffect.NONE: 0.55, SideEffect.ONE_REQUIRED: 0.25,
                    SideEffect.ONE_FORBIDDEN: 0.20},
    "information": {Information.COMPLETE: 0.60, Information.ONE_MISSING: 0.25,
                    Information.CONTRADICTORY: 0.15},
    "scale":       {1: 0.45, 3: 0.35, 12: 0.20},
}


@dataclass
class ParamSpace:
    """Which axes a template actually varies, and how it draws values.

    A template that has no evidence requirement should not sample the
    evidence axis: it would produce three labels for one situation and
    dilute every rate computed over it.
    """
    axes: tuple[str, ...] = ("entity", "amount_band", "evidence",
                             "side_effect", "information", "scale")
    weights: dict[str, dict[Any, float]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_WEIGHTS.items()})
    draw_values: Callable[[Params, random.Random, Firm], dict[str, Any]] | None = None

    def sample(self, seed: int, firm: Firm) -> Params:
        rng = random.Random(seed)
        chosen: dict[str, Any] = {}
        for axis in self.axes:
            table = self.weights.get(axis) or DEFAULT_WEIGHTS[axis]
            options, wts = list(table), [table[o] for o in table]
            chosen[axis] = rng.choices(options, weights=wts, k=1)[0]
        params = Params(seed=seed, **chosen)
        if self.draw_values is not None:
            params = Params(seed=seed, **chosen,
                            values=self.draw_values(params, rng, firm))
        return params

    def enumerate_axes(self) -> Iterator[dict[str, Any]]:
        """The full grid, for coverage checks rather than for sampling."""
        import itertools

        tables = [list(self.weights.get(a) or DEFAULT_WEIGHTS[a]) for a in self.axes]
        for combo in itertools.product(*tables):
            yield dict(zip(self.axes, combo))


@dataclass
class Instance:
    """A rendered task: what to ask, and how it will be judged.

    Assertions and envelope are built here, before the model runs, and are
    never regenerated afterwards (SPEC §10.9).
    """
    template_id: str
    firm_id: str
    params: Params
    instruction: str
    assertions: list[Assertion]
    envelope: MutationEnvelope
    setup: Callable[[Any], None] | None = None
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def params_seed(self) -> int:
        return self.params.seed


@dataclass
class WorkflowTemplate:
    template_id: str
    render_instruction: Callable[[Params, Firm], str]
    generate_assertions: Callable[[Params, Firm], list[Assertion]]
    generate_envelope: Callable[[Params, Firm], MutationEnvelope]
    param_space: ParamSpace
    # Optional world-preparation: create the near-duplicate pair an
    # `ambiguous` instance needs, or remove the record a `missing` one needs
    # to be absent. Runs after reset, before the agent starts.
    prepare: Callable[[Any, Params, Firm], None] | None = None
    title: str = ""
    tags: tuple[str, ...] = ()

    def instantiate(self, seed: int, firm: Firm) -> Instance:
        params = self.param_space.sample(seed, firm)
        return Instance(
            template_id=self.template_id, firm_id=firm.firm_id, params=params,
            instruction=self.render_instruction(params, firm),
            assertions=self.generate_assertions(params, firm),
            envelope=self.generate_envelope(params, firm),
            setup=(lambda ad: self.prepare(ad, params, firm)) if self.prepare else None,
            notes={"axes": params.axes(), "title": self.title,
                   "tags": list(self.tags)})

    def instances(self, seeds: list[int], firm: Firm) -> list[Instance]:
        return [self.instantiate(s, firm) for s in seeds]


class TemplateRegistry:
    """Templates, and the split they belong to.

    Calibration templates are quarantined (SPEC §2): they may serve as
    training data, but they may never appear in a reported figure. The
    registry keeps the two sets apart so a reporting path cannot pick up a
    calibration template by accident.
    """

    def __init__(self) -> None:
        self._calibration: dict[str, WorkflowTemplate] = {}
        self._evaluation: dict[str, WorkflowTemplate] = {}

    def register(self, template: WorkflowTemplate, split: str) -> WorkflowTemplate:
        if split not in ("calibration", "evaluation"):
            raise ValueError(f"unknown split {split!r}")
        target = (self._calibration if split == "calibration"
                  else self._evaluation)
        if template.template_id in target:
            raise ValueError(f"duplicate template {template.template_id!r}")
        other = (self._evaluation if split == "calibration" else self._calibration)
        if template.template_id in other:
            raise ValueError(
                f"{template.template_id!r} is already in the other split; a "
                "template may not appear in both")
        target[template.template_id] = template
        return template

    @property
    def calibration(self) -> list[WorkflowTemplate]:
        return list(self._calibration.values())

    @property
    def evaluation(self) -> list[WorkflowTemplate]:
        return list(self._evaluation.values())

    def get(self, template_id: str) -> WorkflowTemplate:
        if template_id in self._evaluation:
            return self._evaluation[template_id]
        if template_id in self._calibration:
            return self._calibration[template_id]
        raise KeyError(template_id)


REGISTRY = TemplateRegistry()


def seeds_for(template_id: str, firm_id: str, n: int,
              salt: str = "") -> list[int]:
    """Deterministic seeds, so an instance is reproducible from its id alone."""
    out = []
    for i in range(n):
        raw = f"{template_id}|{firm_id}|{salt}|{i}".encode()
        out.append(int(hashlib.sha1(raw).hexdigest()[:8], 16))
    return out
