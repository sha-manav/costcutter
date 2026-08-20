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


def test_no_added_template_writes_an_eval_record_type():
    """A new OBSERVE template on an EVAL record type would leak the answer."""
    from shadow.bench.tasks import EVAL_TEMPLATE_IDS, PINNED_OBSERVE, TEMPLATES

    eval_record_types = {"supplier", "item", "sales order", "sales invoice"}
    for tmpl in TEMPLATES:
        if tmpl.id not in PINNED_OBSERVE:
            continue
        subject = tmpl.title.lower()
        assert not any(rt in subject for rt in eval_record_types), (
            f"{tmpl.id} writes a record type an EVAL template writes")
