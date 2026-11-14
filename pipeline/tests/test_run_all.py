import pytest

from pipeline import run_all


def test_help_lists_every_step(capsys):
    with pytest.raises(SystemExit) as exc:
        run_all.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name, _, _ in run_all.STEPS:
        assert name in out
    assert "all" in out


def test_unimplemented_step_reports_its_phase(capsys):
    assert run_all._not_yet("future-step", 9)(None) == 1
    assert "Phase 9" in capsys.readouterr().out


def test_every_listed_step_is_implemented():
    assert {name for name, _, _ in run_all.STEPS} == set(run_all.IMPLEMENTED)


def test_all_runs_implemented_steps_in_order(monkeypatch, capsys):
    order = []
    monkeypatch.setattr(
        run_all,
        "IMPLEMENTED",
        {"fetch": lambda a: order.append("fetch") or 0, "validate": lambda a: order.append("validate") or 0},
    )
    assert run_all.main(["all"]) == 0
    assert order == ["fetch", "validate"]


def test_all_stops_at_first_failure(monkeypatch):
    order = []
    monkeypatch.setattr(
        run_all,
        "IMPLEMENTED",
        {"fetch": lambda a: order.append("fetch") or 2, "validate": lambda a: order.append("validate") or 0},
    )
    assert run_all.main(["all"]) == 2
    assert order == ["fetch"]


def test_all_skips_the_hand_run_steps(monkeypatch, capsys):
    order = []
    steps = {name: (lambda n: (lambda a: order.append(n) or 0))(name) for name, _, _ in run_all.STEPS}
    monkeypatch.setattr(run_all, "IMPLEMENTED", steps)
    assert run_all.main(["all"]) == 0
    assert not set(order) & set(run_all.MANUAL)
    assert order == [name for name, _, _ in run_all.STEPS if name not in run_all.MANUAL]
    assert "Not run by 'all'" in capsys.readouterr().out
