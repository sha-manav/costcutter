"""Round 0 — teacher traces on the corrected harness, Firm A.

SPEC §6. The base model has none of the behaviour this is meant to teach, so
composition matters more than count and is allocated rather than sampled:

    ~40%  hard recovery      an action fails and a later one succeeds on the
                             same subgoal. The highest-value data in the
                             project: the predecessor's whole failure mode
                             was models burning their step budget looping
                             instead of reading the error and changing
                             approach.
    ~25%  policy-sensitive   thresholds, evidence, escalation, abstention,
                             and the counterfactual templates.
    ~25%  ordinary execution CRUD, child tables, document linkage.
    ~10%  ambiguous/missing  the correct outcome is to write nothing and say
                             why.

**Rejection sampling is on full success, envelope included.** Every required
assertion passes, no forbidden mutation, no unexpected one. A trajectory that
reaches the goal by violating policy is not training data — it is the exact
behaviour T3 exists to remove, and letting it in would teach the opposite of
the thing being measured.

**Curriculum stage is assigned at generation time**, not inferred later.
Retrofitting it means re-reading trajectories and guessing intent, and intent
is not recoverable from an action log.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from erpbench.firms import get_firm
from erpbench.templates import (
    AmountBand, EntityPresence, Evidence, Params, REGISTRY, WorkflowTemplate,
    seeds_for)

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
TRACES = ARTIFACTS / "teacher_traces.jsonl"

# Deliberate, not uniform. The shares are the point of Round 0.
COMPOSITION: dict[str, float] = {
    "hard_recovery": 0.40,
    "policy": 0.25,
    "execution": 0.25,
    "abstention": 0.10,
}

# Curriculum stage per category. T1 leads because recovery is behavioural and
# the base model does not have it; T2 is competence; T3 is policy.
STAGE_OF: dict[str, str] = {
    "hard_recovery": "T1",
    "execution": "T2",
    "policy": "T3",
    "abstention": "T3",
}


@dataclass
class TraceSpec:
    """One planned rollout: what to run, and what it is meant to produce."""
    category: str
    stage: str
    template: WorkflowTemplate
    params_override: dict[str, Any] = field(default_factory=dict)
    seed: int = 0

    @property
    def template_id(self) -> str:
        return self.template.template_id


def _by_tag(*tags: str) -> list[WorkflowTemplate]:
    return [t for t in REGISTRY.evaluation
            if any(tag in t.tags for tag in tags)]


def plan(total: int, rng_seed: int = 0) -> list[TraceSpec]:
    """Allocate rollouts across the four categories.

    Hard-recovery instances are not produced by asking a model to fail. They
    are produced by drawing parameters where the first plausible action does
    not work -- a referenced record that is absent, evidence that is stale, a
    total that trips a threshold -- and then keeping only the trajectories in
    which the model recovered. Inducing the failure by tampering with the
    harness would teach recovery from a situation the harness does not
    actually produce.
    """
    import erpbench.evaluation            # noqa: F401
    import erpbench.evaluation_extra      # noqa: F401

    rng = random.Random(rng_seed)
    specs: list[TraceSpec] = []

    pools: dict[str, list[WorkflowTemplate]] = {
        # Templates whose parameter space can put the world in a state where
        # the obvious first action fails.
        "hard_recovery": _by_tag("entity", "child_table", "evidence", "update"),
        "policy": _by_tag("threshold", "counterfactual", "evidence"),
        "execution": _by_tag("write", "child_table"),
        "abstention": _by_tag("abstention", "ambiguous", "information"),
    }
    # Parameter draws that make the first attempt fail, per category.
    overrides: dict[str, list[dict[str, Any]]] = {
        "hard_recovery": [
            {"entity": EntityPresence.MISSING},
            {"evidence": Evidence.STALE},
            {"entity": EntityPresence.AMBIGUOUS},
        ],
        "policy": [
            {"amount_band": AmountBand.ABOVE},
            {"amount_band": AmountBand.AT},
            {"evidence": Evidence.ABSENT},
        ],
        "execution": [{"entity": EntityPresence.EXISTS,
                       "amount_band": AmountBand.BELOW}],
        "abstention": [
            {"entity": EntityPresence.AMBIGUOUS},
            {"entity": EntityPresence.MISSING},
        ],
    }

    for category, share in COMPOSITION.items():
        n = round(total * share)
        pool = pools[category] or list(REGISTRY.evaluation)
        for i in range(n):
            template = pool[i % len(pool)]
            specs.append(TraceSpec(
                category=category,
                stage=STAGE_OF[category],
                template=template,
                params_override=rng.choice(overrides[category]),
                seed=seeds_for(template.template_id, "A", 1,
                               salt=f"teacher{i}")[0]))
    rng.shuffle(specs)
    return specs


def plan_topup(n_execution: int, n_policy_write: int,
               rng_seed: int = 7) -> list[TraceSpec]:
    """Draws the original round is short of, both of them write-completing.

    Round 0 allocated by *category* and let the parameter draw fall where it
    would. On policy-sensitive templates the draw usually landed somewhere the
    policy refuses, so the corpus came out overwhelmingly refusal-terminating:
    with stage read from the draw, T3 holds 215 traces of which 26% complete a
    write, and T1 and T2 together hold 44.

    Two deliberate shapes fix that, and neither is a new kind of task -- both
    are parameter draws the existing templates already support:

    `execution`   non-policy templates drawn at entity=EXISTS,
                  evidence=PRESENT, amount below threshold. The correct
                  outcome is to complete the write. This is what T1 and T2
                  are supposed to be made of.

    `policy_write` policy-sensitive templates drawn so the policy *permits*
                  the action -- under the threshold, evidence present, entity
                  exists. The correct outcome is a compliant write. A policy
                  stage assembled only from cases the policy refuses teaches
                  refusal, not policy; these are the other half.
    """
    import erpbench.evaluation            # noqa: F401
    import erpbench.evaluation_extra      # noqa: F401

    rng = random.Random(rng_seed)
    specs: list[TraceSpec] = []

    permitting = {"entity": EntityPresence.EXISTS,
                  "amount_band": AmountBand.BELOW,
                  "evidence": Evidence.PRESENT}

    execution_pool = [t for t in _by_tag("write", "child_table", "update")
                      if not policy_sensitive(t.template_id)]
    policy_pool = [t for t in REGISTRY.evaluation
                   if policy_sensitive(t.template_id)]

    for label, pool, n in (("execution", execution_pool, n_execution),
                           ("policy_write", policy_pool, n_policy_write)):
        if not pool:
            continue
        for i in range(n):
            template = pool[i % len(pool)]
            specs.append(TraceSpec(
                category=label,
                # Advisory only. stage_for_params reads the draw, and these
                # draws are exactly the ones it routes to execution and to a
                # complying policy case.
                stage="T2" if label == "execution" else "T3",
                template=template,
                params_override=dict(permitting),
                seed=seeds_for(template.template_id, "A", 1,
                               salt=f"topup{label}{i}")[0]))
    rng.shuffle(specs)
    return specs


def instantiate(spec: TraceSpec):
    """Build the instance, honouring the category's parameter override."""
    firm = get_firm("A")
    base = spec.template.param_space.sample(spec.seed, firm)
    params = Params(seed=spec.seed,
                    entity=spec.params_override.get("entity", base.entity),
                    amount_band=spec.params_override.get("amount_band",
                                                         base.amount_band),
                    evidence=spec.params_override.get("evidence", base.evidence),
                    side_effect=base.side_effect,
                    information=base.information,
                    scale=base.scale,
                    values=base.values)
    from erpbench.templates import Instance

    return Instance(
        template_id=spec.template.template_id, firm_id="A", params=params,
        instruction=spec.template.render_instruction(params, firm),
        assertions=spec.template.generate_assertions(params, firm),
        envelope=spec.template.generate_envelope(params, firm),
        setup=(lambda ad: spec.template.prepare(ad, params, firm))
        if spec.template.prepare else None,
        notes={"axes": params.axes(), "category": spec.category,
               "stage": spec.stage})


def is_hard_recovery(row: dict[str, Any]) -> bool:
    """An action failed and a later one succeeded on the same subgoal.

    Read from the instrumentation rather than from the category label: a
    rollout planned as hard-recovery in which nothing actually failed is
    ordinary execution, and mislabelling it would put the wrong data in T1.
    """
    return int(row.get("behaviour", {}).get("recovery_events", 0)) > 0


def accept(row: dict[str, Any]) -> tuple[bool, str]:
    """Rejection sampling. Full success, envelope included."""
    if row.get("status") != "ok":
        return False, f"status={row.get('status')}"
    verdict = row.get("verdict", {})
    if not verdict.get("success"):
        env = verdict.get("envelope", {})
        if env.get("forbidden"):
            return False, "forbidden mutation"
        if env.get("unexpected"):
            return False, "unexpected mutation"
        return False, "assertion failed"
    return True, "ok"


def requires_write(template_id: str, firm_id: str, seed: int,
                   axes: dict[str, Any]) -> bool:
    """Does the correct outcome for this parameter draw include a write?

    Regenerated from the template, the firm and the recorded axes -- all of
    which are fixed before the model runs. The envelope's `required` list is
    what the scorer will demand, so this is the same notion of "correct" the
    benchmark uses, read off the draw rather than off the rollout.
    """
    import erpbench.evaluation            # noqa: F401
    import erpbench.evaluation_extra      # noqa: F401

    from erpbench.firms import get_firm
    from erpbench.templates import (
        AmountBand, EntityPresence, Evidence, Params, REGISTRY)

    template = {t.template_id: t for t in REGISTRY.evaluation}[template_id]
    firm = get_firm(firm_id)
    base = template.param_space.sample(seed, firm)
    params = Params(seed=seed,
                    entity=EntityPresence(axes["entity"]),
                    amount_band=AmountBand(axes["amount_band"]),
                    evidence=Evidence(axes["evidence"]),
                    side_effect=base.side_effect,
                    information=base.information,
                    scale=axes["scale"], values=base.values)
    return bool(template.generate_envelope(params, firm).required)


# Templates whose correct outcome turns on the firm's policy rather than on
# the state of the world: thresholds, evidence rules, and the counterfactual
# pairs where the same instruction is required at one firm and forbidden at
# another.
POLICY_TAGS = ("threshold", "counterfactual", "evidence")


def policy_sensitive(template_id: str) -> bool:
    import erpbench.evaluation            # noqa: F401
    import erpbench.evaluation_extra      # noqa: F401

    from erpbench.templates import REGISTRY

    template = {t.template_id: t for t in REGISTRY.evaluation}.get(template_id)
    return bool(template and any(t in template.tags for t in POLICY_TAGS))


# Draws on which the first plausible action cannot succeed. These are what
# make a trace recovery data -- the model has to read an error and change
# approach -- and they are properties of the draw, not of what happened.
_OBSTRUCTED_ENTITY = {"missing", "ambiguous"}
_OBSTRUCTED_EVIDENCE = {"stale", "absent"}


def stage_for_params(row: dict[str, Any]) -> str:
    """Curriculum stage, from the parameter draw alone.

    The rule this replaces inferred intent from the trajectory's terminal
    state, and that is what destroyed the trained model. `restage` sent every
    planned-T1 trace that did not recover into T2, the execution stage. But
    hard-recovery instances are drawn deliberately with `entity=MISSING`,
    `evidence=STALE` or `entity=AMBIGUOUS` so the first plausible action
    fails, and on many of those draws the correct outcome is not to recover at
    all -- it is to escalate. Such a trace passes rejection sampling cleanly,
    because declining an impossible task is a full success. So "did not
    recover" was silently equated with "ordinary execution": 84 of T2's 129
    examples arrived that way, leaving the execution stage 61%
    refusal-terminating and 34% write-containing, against T1 at 100% and 100%.
    The model trained on it stopped writing entirely.

    The draw already contains the answer. Whether the correct outcome is a
    write, a recovery or an abstention is fixed by the template, the firm and
    the parameters before the model is invoked, so stage is read from those:

        no write required            -> T3  policy: the answer is to decline
        write required, obstructed   -> T1  recovery: first action must fail
        write required, clean        -> T2  execution: ordinary completion

    Nothing here consults the actions the model took. A trace that was meant
    to teach execution and instead refused is not reclassified into another
    stage -- it is simply a rollout that failed to demonstrate its lesson, and
    rejection sampling already drops it.
    """
    axes = row.get("axes") or {}
    if not axes:
        return row.get("planned_stage") or "T2"
    # Policy-sensitive draws go to the policy stage whichever way the policy
    # resolves them. A threshold case that comes out *under* the limit, where
    # the correct answer is a compliant write, teaches the policy exactly as
    # much as one that comes out over it and must be escalated -- and a T3
    # built only from the refusals is a stage that teaches only stopping.
    if policy_sensitive(row["template_id"]):
        return "T3"
    if not requires_write(row["template_id"], row["firm_id"],
                          int(row["params_seed"]), axes):
        return "T3"
    obstructed = (axes.get("entity") in _OBSTRUCTED_ENTITY
                  or axes.get("evidence") in _OBSTRUCTED_EVIDENCE)
    return "T1" if obstructed else "T2"


def terminal_action(row: dict[str, Any]) -> str:
    """The last action the rollout actually took."""
    actions = row.get("actions") or []
    if not actions:
        return "none"
    last = actions[-1].get("action")
    if isinstance(last, dict):
        last = last.get("action")
    return last if isinstance(last, str) else "none"


def restage(row: dict[str, Any], planned: str) -> str:
    """Final curriculum stage, corrected against what the rollout did.

    A rollout planned for T1 that never failed is T2 data. A rollout planned
    for T2 that recovered from a real error is T1 data, and is worth more
    there than the plan is worth.

    The refusal clause is not a refinement; it repairs a defect that destroyed
    the trained model. Hard-recovery instances are drawn deliberately with
    `entity=MISSING`, `evidence=STALE` or `entity=AMBIGUOUS` so the first
    plausible action fails. When the model does not recover from those draws,
    the correct outcome is usually to escalate or abstain -- and such a trace
    passes rejection sampling cleanly, because declining an impossible task is
    a full success. The old rule sent every one of them to T2 on the grounds
    that it was "not recovery", silently equating *did not recover* with
    *ordinary execution*. 84 of T2's 129 examples arrived that way, leaving
    the execution stage 61% refusal-terminating and only 34% containing a
    write, against T1 at 100% and 100%. The model trained on it stopped
    writing at all: 0/175 on tasks requiring a write, down from 6/175
    untrained.

    So stage is assigned by what the trace demonstrates, not by what it
    failed to demonstrate. A trace that ends in a refusal is policy data
    wherever it was planned.
    """
    if is_hard_recovery(row):
        return "T1"
    if terminal_action(row) in ("escalate", "abstain"):
        return "T3"
    if planned == "T1":
        return "T2"
    return planned
