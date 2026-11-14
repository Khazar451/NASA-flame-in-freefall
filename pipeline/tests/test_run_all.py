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
