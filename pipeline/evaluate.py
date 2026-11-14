"""Score /ask on the question set in pipeline/review/eval_questions.yaml.

    python -m pipeline.evaluate                 retrieval only: are the facts in what the AI is given?
    python -m pipeline.evaluate --provider nim  also the written answers, from the NVIDIA model
    python -m pipeline.evaluate --provider env  also the written answers, from the configured chain

Three numbers per run:
  context facts  share of required facts present in the sources the AI is given
  answer facts   share of required facts present in the final answer
  count openers  answers whose first sentence is a test count, not an answer to the question
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import yaml

from pipeline import ask, config, llm

QUESTIONS = config.PIPELINE_DIR / "review" / "eval_questions.yaml"
COUNT_OPENER = re.compile(r"^\s*(in the psi test tables, )?\d+ tests? match", re.IGNORECASE)


def found(facts: list[str], text: str) -> list[bool]:
    return [bool(re.search(f, text, re.IGNORECASE)) for f in facts]


def context_text(question: str, kb: ask.KnowledgeBase) -> str:
    q = ask.read_question(question, kb)
    if q.partial_gravity:
        return ask.partial_gravity_answer(question, kb)["answer"]
    table_text, _, passages = ask.gather(question, q, kb)
    return "\n".join([table_text or ""] + [p["text"] for p in passages])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.evaluate")
    parser.add_argument("--provider", choices=["none", "nim", "env"], default="none")
    parser.add_argument("--out", type=Path, help="write per-question results as JSON")
    args = parser.parse_args(argv)

    from pipeline.search import SearchIndex

    kb = ask.KnowledgeBase.load(search=SearchIndex.load())
    kb.demo = {}  # score live answers, not the saved demo ones
    provider = None
    if args.provider == "nim":
        provider = llm._openai_provider(config.env("OPENAI_MODEL") or "")
    elif args.provider == "env":
        provider = llm.get_provider()
    budget = ask.Budget(1000)

    items = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    results, context_hits, answer_hits, total, openers = [], 0, 0, 0, 0
    for item in items:
        question, facts = item["question"], item["facts"]
        ctx = found(facts, context_text(question, kb))
        row = {"question": question, "context": ctx}
        if provider is not None:
            started = time.monotonic()
            reply = ask.answer(question, kb, provider, budget)
            row.update(answer=reply["answer"], mode=reply["mode"], seconds=round(time.monotonic() - started, 1),
                       facts=found(facts, reply["answer"]), count_opener=bool(COUNT_OPENER.match(reply["answer"])))
            answer_hits += sum(row["facts"])
            openers += row["count_opener"]
        context_hits += sum(ctx)
        total += len(facts)
        results.append(row)
        mark = "".join("+" if hit else "-" for hit in (row.get("facts") or ctx))
        print(f"  {mark:5} {question}" + (f"  [{row['mode']}, {row['seconds']} s]" if "mode" in row else ""))
    print(f"\ncontext facts: {context_hits}/{total} ({100 * context_hits / total:.0f} %)")
    if provider is not None:
        print(f"answer facts:  {answer_hits}/{total} ({100 * answer_hits / total:.0f} %)")
        print(f"count openers: {openers}/{len(items)}")
    if args.out:
        args.out.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
