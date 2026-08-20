"""Rule 2, enforced structurally: no demonstration traffic on EVAL templates."""
from __future__ import annotations

import pytest

from shadow.bench.tasks import (
    HeldOutViolation, TEMPLATES, all_tasks, assert_observe_only, eval_tasks,
    load_split, make_split, observe_tasks,
)
from shadow.config import get_config


def test_split_is_over_templates_and_disjoint():
    split = load_split(get_config())
    assert set(split.observe) & set(split.eval) == set()
    assert set(split.observe) | set(split.eval) == {t.id for t in TEMPLATES}


def test_split_is_deterministic():
    a, b = make_split(), make_split()
    assert a.observe == b.observe and a.eval == b.eval


def test_generator_refuses_eval_templates():
    split = load_split(get_config())
    for template_id in split.eval:
        with pytest.raises(HeldOutViolation):
            assert_observe_only(template_id)


def test_generator_accepts_observe_templates():
    for template_id in load_split(get_config()).observe:
        assert_observe_only(template_id)


def test_observe_and_eval_tasks_do_not_overlap():
    obs = {t.id for t in observe_tasks()}
    ev = {t.id for t in eval_tasks()}
    assert obs & ev == set()
    assert obs | ev == {t.id for t in all_tasks()}


def test_registry_size():
    assert len(TEMPLATES) >= 12
    assert len(all_tasks()) >= 40


def test_generate_traffic_module_guards_every_task():
    """The generator must call the guard for every task it would drive."""
    import inspect

    from shadow.bench import generate_traffic

    source = inspect.getsource(generate_traffic.generate)
    assert "assert_observe_only" in source
