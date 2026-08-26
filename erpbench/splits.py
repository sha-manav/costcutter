"""Holdout splits — SPEC §4 and §10.10.

Three holdouts, reported separately and never merged:

    template-level   templates never seen in training. The real
                     generalization number, and the one that matters.
    instance-level   templates seen in training, parameter draws not seen.
                     In-distribution, and easier by construction.
    Firm C           frozen in week 1, evaluated once, absent from training
                     and from method selection.

**Frozen before any training data exists.** That ordering is the whole
point: a split decided after trajectories are generated can be chosen —
consciously or not — to flatter the result, and nothing in the numbers would
show it. Assignment is derived from a hash of the template id and a fixed
salt, so it is reproducible, auditable, and not hand-picked.

The salt is a constant in this file. Changing it reassigns every template,
which is why `SPLIT_FINGERPRINT` is asserted by a test: a silent reshuffle
after training has begun would turn a template-level holdout into training
data without anything appearing wrong.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from erpbench.templates import REGISTRY

# Fixed at the moment of freezing. Never change it: reassignment after
# training starts silently converts holdout templates into seen ones.
SPLIT_SALT = "erpbench-week2-freeze"

# Ten templates added after week 2 to raise power on the template-level
# holdout, which is the one measurement that cannot be strengthened once
# training begins. They are held out unconditionally rather than by hash:
# there is no per-template choice to make, so none can be placed by how it
# performs. See erpbench/evaluation_extra.py for the full reasoning and the
# limitation that the author had seen the week-2 bucket results.
FORCED_HOLDOUT_PREFIXES = tuple(f"E{n}_" for n in range(41, 51))

# Roughly a third held out. Enough templates for a usable interval on the
# generalization number, while leaving the majority available to train on.
TEMPLATE_HOLDOUT_FRACTION = 0.325

# Instance-level holdout: seeds are drawn per (template, firm) and indexed.
# Trials at or above this index are never used for training data, so the
# in-distribution number is measured on draws the model has not seen.
INSTANCE_HOLDOUT_FROM_TRIAL = 2


def _score(template_id: str) -> float:
    """Deterministic position in [0, 1) for a template."""
    digest = hashlib.sha256(f"{SPLIT_SALT}|{template_id}".encode()).hexdigest()
    return int(digest[:12], 16) / float(1 << 48)


def _is_forced(template_id: str) -> bool:
    return template_id.startswith(FORCED_HOLDOUT_PREFIXES)


def template_holdout(template_ids: list[str] | None = None) -> list[str]:
    """Templates reserved from training.

    The original 40 are assigned by hash so none could be hand-picked. The
    ten added after week 2 are forced, because they exist only to add power
    to this bucket and are all held out -- a hash would have scattered most
    of them into the training split and defeated the purpose.
    """
    ids = sorted(template_ids if template_ids is not None
                 else [t.template_id for t in REGISTRY.evaluation])
    forced = [t for t in ids if _is_forced(t)]
    hashed = [t for t in ids if not _is_forced(t)]
    ranked = sorted(hashed, key=_score)
    n = round(len(ranked) * TEMPLATE_HOLDOUT_FRACTION)
    return sorted(ranked[:n] + forced)


def is_template_holdout(template_id: str) -> bool:
    return template_id in set(template_holdout())


def is_instance_holdout(trial_idx: int) -> bool:
    return trial_idx >= INSTANCE_HOLDOUT_FROM_TRIAL


def split_of(template_id: str, firm_id: str, trial_idx: int) -> str:
    """Which reported bucket a row belongs to.

    Order matters. Firm C outranks everything: it is evaluated once and must
    never be absorbed into another bucket. Template-level outranks
    instance-level, because a template the model never saw is a stronger
    claim than an unseen draw of a template it did.
    """
    if firm_id in ("C",):
        return "firm_c"
    if is_template_holdout(template_id):
        return "template_holdout"
    if is_instance_holdout(trial_idx):
        return "instance_holdout"
    return "train_visible"


def manifest() -> dict[str, Any]:
    held = template_holdout()
    every = sorted(t.template_id for t in REGISTRY.evaluation)
    return {
        "salt": SPLIT_SALT,
        "template_holdout_fraction": TEMPLATE_HOLDOUT_FRACTION,
        "instance_holdout_from_trial": INSTANCE_HOLDOUT_FROM_TRIAL,
        "evaluation_templates": every,
        "template_holdout": held,
        "train_visible": [t for t in every if t not in set(held)],
        "blind_firm": "C",
        "forced_holdout_prefixes": list(FORCED_HOLDOUT_PREFIXES),
    }


def fingerprint() -> str:
    """Identity of the whole split assignment."""
    raw = json.dumps(manifest(), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def freeze(dest: Path | None = None) -> Path:
    dest = dest or (Path(__file__).resolve().parent.parent / "artifacts"
                    / "splits_frozen.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(manifest(), fingerprint=fingerprint())
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return dest


if __name__ == "__main__":
    import erpbench.evaluation                              # noqa: F401
    import erpbench.evaluation_extra                        # noqa: F401

    path = freeze()
    m = manifest()
    print(f"frozen -> {path}")
    print(f"  fingerprint      {fingerprint()}")
    print(f"  template holdout {len(m['template_holdout'])}/"
          f"{len(m['evaluation_templates'])}")
    for tid in m["template_holdout"]:
        print(f"    {tid}")
