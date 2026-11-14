"""One entry point for every pipeline step.

    python -m pipeline.run_all --help      list steps
    python -m pipeline.run_all <step>      run one step
    python -m pipeline.run_all all         rebuild everything: every step in order, except
                                           the hand-run steps in MANUAL

Steps are registered in IMPLEMENTED as each phase lands; until then they report
which phase will add them.
"""

from __future__ import annotations

import argparse
from typing import Callable

from pipeline import (
    ask,
    build_index,
    coverage,
    extract_investigations,
    extract_text,
    fetch,
    inventory,
    transform_tests,
    validate,
    varied,
)

# (name, phase that implements it, help text), in pipeline order. validate runs last,
# on the finished output files.
STEPS: list[tuple[str, int, str]] = [
    ("inventory", 1, "list PSI combustion investigations into inventory.csv"),
    ("fetch", 3, "download experimental tables and PDF documents into data/raw/"),
    ("extract-text", 3, "PDFs to page-numbered text in data/interim/text/"),
    ("extract-investigations", 4, "build investigations.json: PSI fields plus AI role and fuel tags"),
    ("review-export", 4, "write the review sheet of AI tags for the ME teammate"),
    ("apply-overrides", 4, "reviewed sheet -> data/overrides.json, then rebuild investigations.json"),
    ("transform-tests", 5, "apply the column mappings in pipeline/mappings to build tests.json"),
    ("coverage", 6, "coverage.json: tests per fuel x oxygen band x pressure band, for the gap map"),
    ("varied", 6, "varied.json: what varied and what stayed the same, per investigation"),
    ("build-index", 7, "embed the document text into the search index for /ask (data/out/search)"),
    ("demo-answers", 7, "answer the demo questions once and save them for /ask"),
    ("validate", 2, "check the output files against the contract and sanity ranges"),
]

StepFn = Callable[[argparse.Namespace], int]

# Steps that involve a person or spend AI quota, so `all` leaves them out:
# review-export / apply-overrides are the domain lead's review loop (the build already
# applies data/overrides.json), and demo-answers makes AI calls and rewrites answers.
MANUAL = ("review-export", "apply-overrides", "demo-answers")

# Filled in phase by phase: {"fetch": fetch.run, ...}
IMPLEMENTED: dict[str, StepFn] = {
    "inventory": inventory.run,
    "fetch": fetch.run,
    "extract-text": extract_text.run,
    "extract-investigations": extract_investigations.run,
    "review-export": extract_investigations.run_review_export,
    "apply-overrides": extract_investigations.run_apply_overrides,
    "transform-tests": transform_tests.run,
    "coverage": coverage.run,
    "varied": varied.run,
    "build-index": build_index.run,
    "demo-answers": ask.run_demo_answers,
    "validate": validate.run,
}

# Extra command-line options a step accepts.
ARGUMENTS: dict[str, Callable[[argparse.ArgumentParser], None]] = {
    "inventory": inventory.add_arguments,
    "fetch": fetch.add_arguments,
    "extract-investigations": extract_investigations.add_arguments,
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
        if fn is None or name in MANUAL:
            continue
        print(f"==> {name}")
        code = fn(args)
        if code != 0:
            print(f"Step '{name}' failed (exit {code}); stopping.")
            return code
        ran += 1
    print(f"Done: {ran} step(s) ran." if ran else "No steps implemented yet.")
    print(f"Not run by 'all' (run them by hand when needed): {', '.join(MANUAL)}")
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
    sub.add_parser("all", help="rebuild everything, except the hand-run steps: " + ", ".join(MANUAL)).set_defaults(
        func=_run_all
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
