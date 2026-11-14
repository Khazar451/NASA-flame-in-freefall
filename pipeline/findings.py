"""Key findings per investigation, taken from NASA's own reports.

For each investigation, the pages most likely to state results (conclusions, summaries,
results sections) are given to the AI, which writes 3 to 5 findings. Each finding must
come with a quote copied word for word from one of those pages. Code then checks that
  * the quote is on that page (spacing, case and PDF ligatures ignored), and
  * every number in the finding also appears in the quote,
and drops any finding that fails. Nothing unchecked reaches data/out/findings.json.

The AI replies are cached, so a rebuild needs no AI calls. /ask searches these findings
first, so "What did BASS-II find?" gets BASS-II's results, not its goals.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field

from pipeline import build_index, config, extract_text, llm
from pipeline.models import FindingsFile

OUT = config.OUT_DIR / "findings.json"
PAGES_PER_INVESTIGATION = 18
PAGES_PER_SOURCE = 8  # so one long dissertation cannot crowd out the final report
CHARS_PER_PAGE = 2000
RESULT_WORDS = re.compile(
    r"\b(conclusions?|summary|results?|findings?|we found|was found|were found|showed|shows that|"
    r"observed|demonstrated|indicate[sd]?|revealed|extinction|persisted)\b", re.IGNORECASE)
GOOD_CATEGORIES = {"Reports": 3, "Science Documents": 2, "Presentations": 1, "PSI record": 1}
TOC = re.compile(r"\.{8,}")
# A result usually names the conditions it holds for, e.g. "34% oxygen, 8.2 psia".
CONDITION = re.compile(r"\d+(?:\.\d+)?\s*(?:%|percent)[^.]{0,40}?\d+(?:\.\d+)?\s*(?:psia?|atm|kpa|cm/s)", re.IGNORECASE)

SYSTEM = """You summarise what NASA microgravity combustion experiments found, for engineers.
Use only the numbered pages you are given. A finding is a result: something measured, observed or
concluded, not a goal, a method or a plan. Write 3 to 6 findings. Each finding is one plain sentence.
For each, copy a supporting quote word for word from one page (at most 300 characters, one continuous
passage, no ellipses) and give that page's label. Every number in a finding must also be in its quote.
If the pages state no results, return an empty list."""


class Item(BaseModel):
    finding: str = Field(min_length=10, max_length=400)
    quote: str = Field(min_length=10, max_length=400)
    page_label: str


class Reply(BaseModel):
    findings: list[Item]


def normal(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                                         "–": "-", "—": "-", "−": "-", " ": " "}))
    return " ".join(text.lower().split())


def candidate_pages(psi_id: str, index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The pages of an investigation most likely to state results, best first."""
    scored = []
    for source_id, info in index.items():
        if info["psi_id"] != psi_id or "error" in info or info.get("scanned"):
            continue
        weight = GOOD_CATEGORIES.get(info["category"])
        if weight is None:
            continue
        final = bool(re.search(r"final|summary|readme|results|dissertation|paper", source_id, re.IGNORECASE))
        # Requirements, plans and procedures say what was meant to happen, not what did.
        planning = bool(re.search(r"requirement|\bSRD\b|\bERD\b|EIRD|plan\b|procedure|operation|protocol|"
                                  r"safety|\bICD\b|concept", source_id, re.IGNORECASE))
        for line in build_index.read_lines(build_index.text_file(source_id, info)):
            text = " ".join(line["text"].split())
            if len(text) < 200 or TOC.search(text):
                continue
            hits = len(RESULT_WORDS.findall(text))
            conclusion = bool(re.search(r"\bconclusions?\b", text, re.IGNORECASE))
            conditions = min(3, len(CONDITION.findall(text)))
            score = weight + hits + 6 * conclusion + 4 * final + 3 * conditions - 8 * planning
            scored.append((score, {"source_id": source_id, "page": line.get("page"),
                                   "section": line.get("section"), "text": text}))
    scored.sort(key=lambda s: -s[0])
    chosen, per_source = [], {}
    for _, page in scored:
        if per_source.get(page["source_id"], 0) < PAGES_PER_SOURCE:
            per_source[page["source_id"]] = per_source.get(page["source_id"], 0) + 1
            chosen.append(page)
        if len(chosen) == PAGES_PER_INVESTIGATION:
            break
    return chosen


def check(item: dict[str, Any], page: dict[str, Any]) -> str | None:
    """Why a finding fails its checks, or None if it passes."""
    from pipeline.ask import numbers_supported

    if normal(item["quote"]).strip(" .\"'") not in normal(page["text"]):
        return "quote not on the page"
    if "..." in item["quote"] or "…" in item["quote"]:
        return "quote is not continuous"
    ok, missing = numbers_supported(item["finding"], item["quote"])
    if not ok:
        return f"numbers not in the quote: {', '.join(missing)}"
    return None


def findings_for(inv: dict[str, Any], pages: list[dict[str, Any]], provider: llm.Provider) -> tuple[list[dict], list[str]]:
    labelled = {f"P{n}": page for n, page in enumerate(pages, start=1)}
    prompt = f"Experiment: {inv['acronym'] or inv['title']} ({inv['id']})\n\n" + "\n\n".join(
        f"[{label}] {page['source_id']}, {'p. ' + str(page['page']) if page['page'] else page.get('section') or 'text'}:\n"
        f"{page['text'][:CHARS_PER_PAGE]}"
        for label, page in labelled.items()
    )
    reply = llm.complete_json(prompt, Reply, system=SYSTEM, provider=provider)
    kept, rejected = [], []
    for item in reply["findings"]:
        # Models write the label as "P11", "[P11]" or just "11".
        number = re.search(r"\d+", item["page_label"])
        page = labelled.get(f"P{number.group(0)}") if number else None
        why = "unknown page label" if page is None else check(item, page)
        if why:
            rejected.append(f"{inv['id']}: {why}: {item['finding'][:80]}")
            continue
        kept.append({
            "investigation_id": inv["id"], "finding": " ".join(item["finding"].split()),
            "quote": " ".join(item["quote"].split()), "source_id": page["source_id"],
            "page": page["page"], "section": page.get("section"),
            "url": f"{inv['url']}#finding-{len(kept) + 1}",
        })
    return kept, rejected


def run(args: argparse.Namespace) -> int:
    if not extract_text.INDEX.exists():
        print("findings: no extracted text; run extract-text first.")
        return 1
    index = json.loads(extract_text.INDEX.read_text(encoding="utf-8"))
    investigations = json.loads((config.OUT_DIR / "investigations.json").read_text(encoding="utf-8"))["investigations"]
    try:
        provider = llm.get_provider()
    except llm.LLMError as exc:
        print(f"findings: {exc}")
        return 1
    all_findings, rejected, failed = [], [], []
    for inv in investigations:
        pages = candidate_pages(inv["id"], index)
        if not pages:
            continue
        try:
            kept, why = findings_for(inv, pages, provider)
        except llm.LLMError as exc:
            failed.append(f"{inv['id']}: {exc}"[:200])
            continue
        all_findings += kept
        rejected += why
        print(f"  {inv['id']:8} {len(kept)} findings kept, {len(why)} rejected")
    payload = FindingsFile.model_validate({
        "meta": {"source": "NASA PSI reports and records; every quote checked against its page",
                 "rejected_by_checks": len(rejected), "investigations_failed": failed},
        "findings": all_findings,
    }).model_dump(mode="json")
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for line in rejected:
        print(f"  rejected: {line}")
    for line in failed:
        print(f"  FAILED: {line}")
    print(f"findings: {len(all_findings)} kept from {len({f['investigation_id'] for f in all_findings})} investigations")
    print(f"Wrote {OUT.relative_to(config.REPO_ROOT)}")
    return 1 if failed else 0
