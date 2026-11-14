"""One entry point for every pipeline step.

    python -m pipeline.run_all --help      list steps
    python -m pipeline.run_all <step>      run one step
    python -m pipeline.run_all all         run every implemented step in order

Steps are registered in IMPLEMENTED as each phase lands; until then they report
which phase will add them.
"""

from __future__ import annotations

import argparse
from typing import Callable

from pipeline import inventory, validate

# (name, phase that implements it, help text), in pipeline order. validate runs last,
# on the finished output files.
STEPS: list[tuple[str, int, str]] = [
    ("inventory", 1, "list PSI combustion investigations into inventory.csv"),
    ("fetch", 3, "download PSI files listed in inventory.csv into data/raw/"),
    ("extract-text", 3, "PDFs to page-numbered text in data/interim/text/"),
    ("extract-investigations", 4, "AI extraction of investigation-level records"),
    ("review-export", 4, "write the review sheet for the ME teammate"),
    ("apply-overrides", 4, "merge reviewed corrections into data/overrides.json"),
    ("propose-mappings", 5, "AI drafts a column mapping YAML for each table"),
    ("transform-tests", 5, "apply reviewed mappings to build tests.json"),
    ("coverage", 6, "coverage grid for the gap map"),
    ("varied", 6, "what varied vs. what stayed the same, per investigation"),
    ("build-index", 7, "build the search index for /ask"),
    ("validate", 2, "check the output files against the contract and sanity ranges"),
]

StepFn = Callable[[argparse.Namespace], int]

# Filled in phase by phase: {"fetch": fetch.run, ...}
IMPLEMENTED: dict[str, StepFn] = {"inventory": inventory.run, "validate": validate.run}

# Extra command-line options a step accepts.
ARGUMENTS: dict[str, Callable[[argparse.ArgumentParser], None]] = {
    "inventory": inventory.add_arguments,
    "validate": validate.add_arguments,
}


def _not_yet(name: str, phase: int) -> StepFn:
    def run(_: argparse.Namespace) -> int:
        print(f"'{name}' is not implemented yet (Phase {phase}).")
        return 1

    return run


def _run_all(args: argparse.Namespace) -> int:
    ran = 0
    for name, _, _ in STEPS:
        fn = IMPLEMENTED.get(name)
        if fn is None:
            continue
        print(f"==> {name}")
        code = fn(args)
        if code != 0:
            print(f"Step '{name}' failed (exit {code}); stopping.")
            return code
        ran += 1
    print(f"Done: {ran} step(s) ran." if ran else "No steps implemented yet.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.run_all",
        description="Flame in Freefall data pipeline: data/raw/ -> data/out/",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<step>")
    for name, phase, help_text in STEPS:
        p = sub.add_parser(name, help=f"{help_text} [Phase {phase}]")
        p.set_defaults(func=IMPLEMENTED.get(name) or _not_yet(name, phase))
        if name in ARGUMENTS:
            ARGUMENTS[name](p)
    sub.add_parser("all", help="run every implemented step in order").set_defaults(func=_run_all)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
