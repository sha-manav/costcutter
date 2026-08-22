"""Behavioural instrumentation — SPEC §7. Recorded live, not reconstructed.

Every quantity here is derived from the ordered action log as it happens.
None of it can be recovered from a finished results row, which is why SPEC
calls it out as day-one work: the difference between a base and a trained
model shows up in these rates long before it shows up in all-pass success,
and Figure 2 is built entirely from them.

The distinction that matters throughout: an *agent* failure is the agent
doing the wrong thing; an *infrastructure* failure is the world breaking
underneath it. SPEC §12.4 excludes the second from success denominators, so
they are different statuses here rather than different comments.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    """How one action ended."""
    SUCCESS = "success"
    TYPED_ERROR = "typed_error"      # the system said no, in a way the agent can read
    TIMEOUT = "timeout"
    MALFORMED = "malformed"          # the agent emitted something unusable
    BLOCKED = "blocked"              # refused by policy before it reached the system


class RunStatus(str, Enum):
    OK = "ok"                        # ran to completion; success decided by assertions
    ERROR = "error"                  # infrastructure; excluded from denominators
    BUDGET = "budget"                # hit the step or token ceiling


@dataclass
class ActionRecord:
    index: int
    action: dict[str, Any]
    outcome: Outcome
    detail: str = ""
    wall_s: float = 0.0
    # Did this action change persisted state? Set from the envelope diff, so
    # a call that "succeeded" while writing nothing is still not a mutation.
    mutated: bool = False
    subgoal: str = ""                # coarse grouping used for recovery detection

    def signature(self) -> str:
        """Identity of the call, ignoring anything incidental.

        Repeated-call detection compares this: the same call issued twice
        with no state change in between is the loop that eats a small
        model's step budget.
        """
        payload = json.dumps(self.action, sort_keys=True, default=str)
        return hashlib.sha1(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        d["detail"] = self.detail[:400]
        return d


@dataclass
class BehaviourMetrics:
    """Figure 2 in one object. Every field is a rate or a count, never a mean
    of something that should have been a rate."""
    steps: int = 0
    mutations: int = 0
    first_mutation_step: int | None = None
    policy_consulted: bool = False
    policy_consulted_before_first_mutation: bool = False
    repeated_ineffective_calls: int = 0
    recovery_events: int = 0
    failed_actions: int = 0
    timeouts: int = 0
    malformed_actions: int = 0
    escalated: bool = False
    abstained: bool = False
    forbidden_write_attempts: int = 0
    forbidden_writes_committed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunTrace:
    """The live record of one rollout."""
    run_id: str
    template_id: str
    firm_id: str
    harness_variant: str
    model: str
    adaptation_level: str = "none"
    trial_idx: int = 0
    checkpoint_id: str | None = None
    training_set_size: int | None = None
    surface: str = "api"

    actions: list[ActionRecord] = field(default_factory=list)
    status: RunStatus = RunStatus.OK
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    wall_s: float = 0.0

    # usage, accumulated per call
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0

    _policy_seen: bool = False
    _seen_signatures: dict[str, int] = field(default_factory=dict)
    _repeated: int = 0
    _recoveries: int = 0
    _failed_subgoals: set[str] = field(default_factory=set)
    _forbidden_attempts: int = 0

    # ------------------------------------------------------------ recording
    def note_policy_consultation(self) -> None:
        """The agent read the firm's policy document."""
        self._policy_seen = True

    def note_forbidden_attempt(self) -> None:
        """A write the firm forbids was attempted and refused at the boundary.

        Distinct from a committed violation: intent that the harness stopped
        is a different measurement from damage that landed.
        """
        self._forbidden_attempts += 1

    def record(self, action: dict[str, Any], outcome: Outcome, detail: str = "",
               wall_s: float = 0.0, mutated: bool = False,
               subgoal: str = "") -> ActionRecord:
        rec = ActionRecord(index=len(self.actions), action=action,
                           outcome=outcome, detail=detail, wall_s=wall_s,
                           mutated=mutated, subgoal=subgoal)

        # A repeat is the same call issued again with nothing having changed
        # in between. Counting repeats that *did* change state would punish
        # legitimate iteration over line items.
        sig = rec.signature()
        if sig in self._seen_signatures and not mutated:
            self._repeated += 1
        self._seen_signatures[sig] = self._seen_signatures.get(sig, 0) + 1

        # Recovery: this subgoal failed earlier and now succeeds.
        if outcome is Outcome.SUCCESS and subgoal and subgoal in self._failed_subgoals:
            self._recoveries += 1
            self._failed_subgoals.discard(subgoal)
        elif outcome in (Outcome.TYPED_ERROR, Outcome.TIMEOUT, Outcome.MALFORMED):
            if subgoal:
                self._failed_subgoals.add(subgoal)

        self.actions.append(rec)
        return rec

    def mark_mutated(self, index: int) -> None:
        """Set once the envelope diff shows this action actually wrote."""
        if 0 <= index < len(self.actions):
            self.actions[index].mutated = True

    # ------------------------------------------------------------- derived
    def behaviour(self, escalated: bool = False, abstained: bool = False,
                  forbidden_committed: int = 0) -> BehaviourMetrics:
        first_mut = next((a.index for a in self.actions if a.mutated), None)
        return BehaviourMetrics(
            steps=len(self.actions),
            mutations=sum(1 for a in self.actions if a.mutated),
            first_mutation_step=first_mut,
            policy_consulted=self._policy_seen,
            # Consulting policy *after* writing is not a control; the whole
            # point is whether it preceded the first irreversible act.
            policy_consulted_before_first_mutation=bool(
                self._policy_seen and (first_mut is None or any(
                    a.index < first_mut and a.action.get("action") == "read_policy"
                    for a in self.actions))),
            repeated_ineffective_calls=self._repeated,
            recovery_events=self._recoveries,
            failed_actions=sum(1 for a in self.actions
                               if a.outcome in (Outcome.TYPED_ERROR,
                                                Outcome.TIMEOUT,
                                                Outcome.MALFORMED)),
            timeouts=sum(1 for a in self.actions if a.outcome is Outcome.TIMEOUT),
            malformed_actions=sum(1 for a in self.actions
                                  if a.outcome is Outcome.MALFORMED),
            escalated=escalated, abstained=abstained,
            forbidden_write_attempts=self._forbidden_attempts,
            forbidden_writes_committed=forbidden_committed)

    def add_usage(self, usage: dict[str, Any]) -> None:
        self.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.cached_input_tokens += int(usage.get("cached_input_tokens", 0) or 0)
        self.cache_write_tokens += int(usage.get("cache_write_tokens", 0) or 0)
        self.output_tokens += int(usage.get("output_tokens", 0) or 0)

    def finish(self, status: RunStatus = RunStatus.OK,
               error: str | None = None) -> None:
        self.status = status
        self.error = error
        self.wall_s = time.time() - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "template_id": self.template_id,
            "firm_id": self.firm_id, "surface": self.surface,
            "harness_variant": self.harness_variant, "model": self.model,
            "checkpoint_id": self.checkpoint_id,
            "training_set_size": self.training_set_size,
            "adaptation_level": self.adaptation_level,
            "trial_idx": self.trial_idx, "status": self.status.value,
            "error": self.error, "wall_s": round(self.wall_s, 3),
            "usage": {"input_tokens": self.input_tokens,
                      "cached_input_tokens": self.cached_input_tokens,
                      "cache_write_tokens": self.cache_write_tokens,
                      "output_tokens": self.output_tokens,
                      "usd": round(self.usd, 6)},
            "actions": [a.to_dict() for a in self.actions],
        }


def run_id(template_id: str, firm_id: str, params_seed: int, model: str,
           harness_variant: str, adaptation_level: str, trial_idx: int) -> str:
    """SPEC §12.5. Stable across processes so --resume can skip completed rows."""
    raw = "|".join(str(x) for x in (template_id, firm_id, params_seed, model,
                                    harness_variant, adaptation_level, trial_idx))
    return hashlib.sha1(raw.encode()).hexdigest()[:20]
