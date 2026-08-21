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


# --------------------------------------------------------------------------
# The held-out set is frozen. These are regression tests on the experiment's
# integrity, not on the code: if EVAL moves, every number measured against an
# earlier version becomes incomparable and the comparison is void.
# --------------------------------------------------------------------------

FROZEN_EVAL = [
    "T03_stock_on_hand",
    "T06_latest_order_total",
    "T09_create_sales_order",
    "T10_create_supplier",
    "T12_create_item",
    "T14_create_sales_invoice",
]


def test_the_held_out_set_is_exactly_these_six():
    from shadow.bench.tasks import EVAL_TEMPLATE_IDS

    assert sorted(EVAL_TEMPLATE_IDS) == sorted(FROZEN_EVAL)
    assert sorted(make_split().eval) == sorted(FROZEN_EVAL)
    assert sorted(load_split(get_config()).eval) == sorted(FROZEN_EVAL)


def test_eval_goals_and_checks_are_unchanged():
    """Not just the ids: the tasks themselves."""
    from shadow.bench.tasks import template

    expected = {
        "T03_stock_on_hand": ("stock_on_hand", "read", "item_code"),
        "T06_latest_order_total": ("latest_order_total", "read", "customer"),
        "T09_create_sales_order": ("sales_order_created", "write", "customer"),
        "T10_create_supplier": ("supplier_created", "write", "supplier_name"),
        "T12_create_item": ("item_created", "write", "item_code"),
        "T14_create_sales_invoice": ("sales_invoice_created", "write", "customer"),
    }
    for template_id, (check, kind, first_param) in expected.items():
        tmpl = template(template_id)
        assert tmpl.check == check
        assert tmpl.kind == kind
        assert first_param in tmpl.param_sets[0]
        assert len(tmpl.param_sets) == 3


def test_pinned_templates_go_to_observe_and_never_to_eval():
    from shadow.bench.tasks import PINNED_OBSERVE, TEMPLATES

    split = make_split()
    known = {t.id for t in TEMPLATES}
    for template_id in PINNED_OBSERVE:
        if template_id in known:
            assert template_id in split.observe
            assert template_id not in split.eval


def test_no_observe_template_writes_an_eval_record_type():
    """An OBSERVE template on an EVAL record type would leak the answer.

    Compared exactly, by declared record type: "Item Group" is not "Item",
    and a substring check would reject it wrongly.
    """
    from shadow.bench.tasks import EVAL_TEMPLATE_IDS, TEMPLATES, template

    eval_writes = {template(t).writes for t in EVAL_TEMPLATE_IDS} - {None}
    assert eval_writes == {"Sales Order", "Supplier", "Item", "Sales Invoice"}

    split = make_split()
    for tmpl in TEMPLATES:
        if tmpl.id not in split.observe or tmpl.writes is None:
            continue
        assert tmpl.writes not in eval_writes, (
            f"{tmpl.id} writes {tmpl.writes!r}, which an EVAL template writes")


def test_every_write_template_declares_what_it_writes():
    from shadow.bench.tasks import TEMPLATES

    for tmpl in TEMPLATES:
        assert (tmpl.writes is not None) == (tmpl.kind == "write"), tmpl.id


def test_indist_instances_are_never_both_observed_and_evaluated():
    """The in-distribution claim rests entirely on this.

    That regime observes and evaluates the same templates, so the only thing
    separating "automating an observed workflow" from "replaying a memorised
    one" is that the parameter values differ. Nothing downstream would notice
    a leak.
    """
    from shadow.bench.indist import (
        INDIST_TEMPLATES, InstanceLeak, check_instance_holdout)

    check_instance_holdout()
    for template in INDIST_TEMPLATES:
        observed = {tuple(sorted(p.items())) for p in template.observe_params}
        evaluated = {tuple(sorted(p.items())) for p in template.eval_params}
        assert observed and evaluated
        assert not (observed & evaluated), template.id

    # And the guard actually fires rather than passing vacuously.
    leaky = INDIST_TEMPLATES[0].__class__(
        id="D99", title="t", goal="g", kind="read", check="c",
        observe_params=({"customer": "Same"},),
        eval_params=({"customer": "Same"},))
    import shadow.bench.indist as mod
    original = mod.INDIST_TEMPLATES
    mod.INDIST_TEMPLATES = (leaky,)
    try:
        with pytest.raises(InstanceLeak):
            check_instance_holdout()
    finally:
        mod.INDIST_TEMPLATES = original


def test_indist_templates_do_not_write_what_eval_writes():
    """Keeps the two evaluations visibly separate.

    The in-distribution set has its own tools and its own traffic, so an
    overlap would not actually contaminate the held-out result -- but a
    reader should not have to take that on trust.
    """
    from shadow.bench.indist import INDIST_TEMPLATES
    from shadow.bench.tasks import EVAL_TEMPLATE_IDS, TEMPLATES

    eval_writes = {t.writes for t in TEMPLATES
                   if t.id in EVAL_TEMPLATE_IDS and t.writes}
    indist_writes = {t.writes for t in INDIST_TEMPLATES if t.writes}
    assert not (eval_writes & indist_writes), (
        f"in-distribution writes overlap held-out writes: "
        f"{sorted(eval_writes & indist_writes)}")
