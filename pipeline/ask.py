"""Phase 7: answer a question about the PSI combustion data.

Every answer is first built without any AI model ("retrieval answer"):
  * key findings: results stated in NASA's reports (data/out/findings.json, each with a
    checked quote), searched first;
  * a table answer: the question is read with plain rules (experiment names, fuel type,
    oxygen and pressure limits, diluent gas) and tests.json is filtered and summarised;
  * document passages: keyword and meaning-based search combined, at most two passages per
    document, planning documents and reference lists ranked last.
When the AI budget allows, Gemini rewrites this into a short answer using only those
sources. The AI answer is kept only if every number in it appears in the sources (or
is a unit conversion of one); otherwise the retrieval answer is returned.

Questions about partial gravity always get a fixed "no data" answer, because PSI has
no partial-gravity tests. Answers to the demo questions are read from a committed file
so the demo never depends on the AI quota.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, Field

from pipeline import config, llm, transform_tests

KPA_PER_PSI = 6.894757
KPA_PER_ATM = 101.325
KPA_PER_BAR = 100.0
KPA_PER_MMHG = 0.1333224
DEMO_ANSWERS = config.PIPELINE_DIR / "review" / "demo_answers.json"
BRIEF_URL = "https://www.spaceappschallenge.org/2026/challenges/flame-in-freefall/?tab=details"
MAX_PASSAGES = 6
MAX_FINDINGS = 4
PASSAGES_PER_SOURCE = 2
PASSAGES_IN_TEXT = 3
# Requirements, plans and procedures say what was meant to happen, not what was found.
PLANNING = re.compile(r"requirement|\bSRD\b|\bERD\b|EIRD|plan\b|procedure|operation|protocol|safety|\bICD\b|"
                      r"concept|drawing", re.IGNORECASE)

# Names people use for each investigation, besides its acronym. Matching is
# case-insensitive except for names that are also ordinary English words.
ALIASES: dict[str, list[str]] = {
    "PSI-10": ["flame design"],
    "PSI-20": ["bre", "burning rate emulator"],
    "PSI-21": ["cld flame", "cld", "coflow laminar diffusion"],
    "PSI-22": ["e-field", "efield", "electric field"],
    "PSI-23": ["s-flame"],
    "PSI-25": ["bass-ii", "bass 2", "bass2"],
    "PSI-26": ["bass"],
    "PSI-39": ["cfi"],
    "PSI-47": ["daft", "daft-2"],
    "PSI-68": ["flex-2", "flex2"],
    "PSI-69": ["flex"],
    "PSI-98": ["saffire-i", "saffire-1"],
    "PSI-99": ["saffire-ii", "saffire-2"],
    "PSI-100": ["saffire-iii", "saffire-3"],
    "PSI-101": ["same-r"],
    "PSI-102": ["same"],
    "PSI-106": ["slice"],
    "PSI-107": ["spice"],
    "PSI-159": ["cfi-g", "cfig"],
}
GROUPS = {
    "acme": ["PSI-10", "PSI-20", "PSI-21", "PSI-22", "PSI-23", "PSI-159"],
    "saffire": ["PSI-98", "PSI-99", "PSI-100"],
}
CASE_SENSITIVE = {"same", "same-r", "slice"}

FUEL_WORDS = {
    "solid": r"solids?|fabrics?|textiles?|cotton|plastics?|pmma|acrylic|sibal|nomex|kapton|teflon|silicone|"
             r"materials? burn|wires?|insulation",
    "liquid": r"liquids?|droplets?|heptane|methanol|ethanol|decane|dodecane",
    "gas": r"gaseous|gas[- ](?:jet|flames?|fuels?)|jet flames?|burners?|ethylene|methane|propane|butane|ethane",
}
DILUENT_WORDS = {"CO2": r"co2|co₂|carbon dioxide", "He": r"helium"}
PARTIAL_GRAVITY = re.compile(
    r"partial[- ]gravity|reduced[- ]gravity|fractional gravity|(?:lunar|moon'?s?|mars|martian) gravity|"
    r"gravity (?:on|of) (?:the )?(?:moon|mars)|1/6\s*g\b|one[- ]sixth g|0\.16\s*g\b|0\.38\s*g\b",
    re.IGNORECASE,
)
HABITATS = {  # (regex, o2 range %, pressure range kPa, words for the answer)
    "lunar": (r"\blunar\b|moon (?:base|habitat)|\brovers?\b", (30.0, 100.0), (29.65, 70.33),
              "lunar-habitat conditions: 30 % oxygen or more at 4.3 to 10.2 psi"),
    "suit": (r"space ?suits?|\beva\b", (95.0, 100.0), (25.0, 35.0),
             "spacesuit conditions: 100 % oxygen at 4.3 psi"),
    "cabin": (r"\bcabin\b|space station|\biss\b|sea[- ]level|earth air|normal air", (19.0, 23.5), (95.0, 110.0),
              "cabin conditions: about 21 % oxygen at 14.7 psi"),
}
MIN_WORDS = r"above|over|more than|greater than|at least|exceeding|higher than|>=?|≥"
MAX_WORDS = r"below|under|less than|at most|lower than|up to|<=?|≤"
AFTER_MIN = r"or (?:more|higher|above)|and (?:above|higher|over)|\+"
AFTER_MAX = r"or (?:less|lower|below)|and (?:below|lower|under)"
QUANTITY = re.compile(r"(\d+(?:\.\d+)?)\s*(%|percent|psi|kpa|atm|bar|mm ?hg)", re.IGNORECASE)
OXYGEN_WORD = re.compile(r"oxygen|\bo2\b|o₂", re.IGNORECASE)
NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
NUMBER_WITH_UNIT = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(%|percent|psi|kpa|atm|bar|mm|cm|mg|s\b|k\b)?", re.IGNORECASE)


class Search(Protocol):
    def query(self, text: str, k: int = 6, psi_ids: Sequence[str] | None = None) -> list[dict[str, Any]]: ...


@dataclass
class Query:
    psi_ids: list[str] = field(default_factory=list)
    fuels: list[str] = field(default_factory=list)
    diluents: list[str] = field(default_factory=list)
    o2: tuple[float | None, float | None] = (None, None)
    pressure: tuple[float | None, float | None] = (None, None)  # kPa
    habitat: str | None = None
    partial_gravity: bool = False

    @property
    def has_filters(self) -> bool:
        return bool(self.psi_ids or self.fuels or self.diluents or self.habitat
                    or any(v is not None for v in (*self.o2, *self.pressure)))


@dataclass
class KnowledgeBase:
    investigations: list[dict[str, Any]]
    tests: list[dict[str, Any]]
    varied: dict[str, str] = field(default_factory=dict)
    search: Search | None = None
    demo: dict[str, dict[str, Any]] = field(default_factory=dict)
    assumed: list[tuple[str, str, set[str]]] = field(default_factory=list)
    gravity_paired: str = ""  # Saffire-II's microgravity vs 1 g summary from gravity.json
    findings: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.by_id = {i["id"]: i for i in self.investigations}
        self._finding_keywords = None
        self._finding_vectors = None

    @classmethod
    def load(cls, out_dir: Path | None = None, search: Search | None = None,
             demo_path: Path | None = None) -> "KnowledgeBase":
        out_dir = out_dir or config.OUT_DIR
        read = lambda name: json.loads((out_dir / name).read_text(encoding="utf-8"))
        varied_path = out_dir / "varied.json"
        varied = {e["investigation_id"]: e["summary"] for e in read("varied.json")["investigations"]} \
            if varied_path.exists() else {}
        demo_path = demo_path or DEMO_ANSWERS
        demo = {normalise(d["question"]): d for d in json.loads(demo_path.read_text(encoding="utf-8"))} \
            if demo_path.exists() else {}
        try:
            assumed = transform_tests.assumed_fields()
        except Exception:  # mappings missing on a slim deployment: fall back to the row notes
            assumed = []
        gravity_paired = read("gravity.json").get("paired_summary", "") if (out_dir / "gravity.json").exists() else ""
        findings = read("findings.json")["findings"] if (out_dir / "findings.json").exists() else []
        kb = cls(read("investigations.json")["investigations"], read("tests.json")["tests"],
                 varied, search, demo, assumed, gravity_paired, findings)
        kb.prepare_findings()  # at startup, not inside the first question
        return kb

    def prepare_findings(self) -> None:
        """Build the small keyword index and the vectors for the findings, once."""
        if not self.findings or self._finding_keywords is not None:
            return
        from pipeline.keyword_index import KeywordIndex

        texts = [f"{f['finding']} {f['quote']}" for f in self.findings]
        self._finding_keywords = KeywordIndex.build(texts)
        if self.search is not None and hasattr(self.search, "embed"):
            import numpy as np

            self._finding_vectors = np.array(self.search.embed(texts), dtype=np.float32)


def normalise(question: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9%.]+", " ", question.lower()).split()).strip(" .")


def name(inv: dict[str, Any]) -> str:
    return (inv.get("acronym") or inv["id"]).replace("ACME/", "ACME ")


def name_with_id(inv: dict[str, Any]) -> str:
    """ "FLEX (PSI-69)", or just "PSI-115" for a study without an acronym."""
    return name(inv) if name(inv) == inv["id"] else f"{name(inv)} ({inv['id']})"


# --------------------------------------------------------------------------- reading


def _alias_patterns(kb: KnowledgeBase) -> list[tuple[re.Pattern[str], list[str]]]:
    entries: list[tuple[str, list[str]]] = []
    for inv in kb.investigations:
        names = set(ALIASES.get(inv["id"], []))
        if inv.get("acronym") and "/" not in inv["acronym"]:
            names.add(inv["acronym"].lower())
        entries += [(n, [inv["id"]]) for n in names]
    entries += [(group, ids) for group, ids in GROUPS.items()]
    patterns = []
    for alias, ids in sorted(entries, key=lambda e: -len(e[0])):
        body = r"[- ]?".join(re.escape(part) for part in re.split(r"[- ]", alias))
        flags = 0 if alias in CASE_SENSITIVE else re.IGNORECASE
        text = alias.upper() if alias in CASE_SENSITIVE else body
        patterns.append((re.compile(rf"(?<![\w-]){text if flags == 0 else body}(?![\w-])", flags), ids))
    return patterns


def _qualifier(text: str, start: int, end: int) -> str | None:
    before, after = text[max(0, start - 30):start].lower(), text[end:end + 18].lower()
    gas = r"(?:oxygen|o2|o₂)?\s*"
    if re.search(rf"(?:{MIN_WORDS})\s*$", before) or re.match(rf"\s*{gas}(?:{AFTER_MIN})", after):
        return "min"
    if re.search(rf"(?:{MAX_WORDS})\s*$", before) or re.match(rf"\s*{gas}(?:{AFTER_MAX})", after):
        return "max"
    return None


def read_question(question: str, kb: KnowledgeBase) -> Query:
    q = Query(partial_gravity=bool(PARTIAL_GRAVITY.search(question)))
    remaining = question
    for pattern, ids in _alias_patterns(kb):
        for match in pattern.finditer(remaining):
            q.psi_ids += [i for i in ids if i not in q.psi_ids]
            remaining = remaining[:match.start()] + " " * (match.end() - match.start()) + remaining[match.end():]
    q.psi_ids += [i for i in re.findall(r"PSI-\d+", question, re.IGNORECASE) if i.upper() in kb.by_id
                  and i.upper() not in q.psi_ids]
    q.psi_ids = [i.upper() for i in q.psi_ids]
    q.fuels = [phase for phase, words in FUEL_WORDS.items() if re.search(rf"\b(?:{words})\b", question, re.I)]
    q.diluents = [gas for gas, words in DILUENT_WORDS.items() if re.search(rf"\b(?:{words})", question, re.I)]

    o2 = [None, None]
    pressure = [None, None]
    exact_o2 = exact_p = None
    for match in QUANTITY.finditer(question):
        value, unit = float(match.group(1)), match.group(2).lower().replace(" ", "")
        kind = _qualifier(question, match.start(), match.end())
        if unit in ("%", "percent"):
            near = question[max(0, match.start() - 25):match.end() + 25]
            if not OXYGEN_WORD.search(near):
                continue
            target, exact = o2, (value - 1.0, value + 1.0)
        else:
            value *= {"psi": KPA_PER_PSI, "kpa": 1.0, "atm": KPA_PER_ATM, "bar": KPA_PER_BAR, "mmhg": KPA_PER_MMHG}[unit]
            target, exact = pressure, (value * 0.95, value * 1.05)
        if kind == "min":
            target[0] = value
        elif kind == "max":
            target[1] = value
        elif target is o2:
            exact_o2 = exact
        else:
            exact_p = exact

    if not q.partial_gravity:
        for habitat, (pattern, o2_range, p_range, _) in HABITATS.items():
            if re.search(pattern, question, re.IGNORECASE):
                q.habitat = habitat
                if o2 == [None, None]:
                    o2, exact_o2 = list(o2_range), None
                if pressure == [None, None]:
                    pressure, exact_p = list(p_range), None
                break
    if exact_o2 and o2 == [None, None]:
        o2 = list(exact_o2)
    if exact_p and pressure == [None, None]:
        pressure = list(exact_p)
    q.o2, q.pressure = (o2[0], o2[1]), (pressure[0], pressure[1])
    return q


# --------------------------------------------------------------------------- table answer


def _num(x: float) -> str:
    return f"{x:.3g}" if abs(x) < 100 else f"{x:.0f}"


def _range(values: list[float], unit: str) -> str:
    lo, hi = min(values), max(values)
    return f"{_num(lo)} {unit}" if _num(lo) == _num(hi) else f"{_num(lo)} to {_num(hi)} {unit}"


def _psi(kpa: float) -> str:
    return _num(kpa / KPA_PER_PSI)


def psi_order(psi_id: str) -> int:
    return int(psi_id.split("-")[1])


def scope_words(q: Query, kb: KnowledgeBase) -> str:
    """" with solid fuels in BASS-II": the filters that are not oxygen or pressure, as words."""
    words = ""
    if q.fuels:
        words += " with " + " or ".join({"solid": "solid fuels", "liquid": "liquid droplets",
                                          "gas": "gas flames"}[f] for f in q.fuels)
    if q.diluents:
        words += " with " + " or ".join(q.diluents) + " dilution"
    if q.psi_ids:
        words += " in " + ", ".join(name(kb.by_id[i]) for i in q.psi_ids)
    return words


def describe_filters(q: Query) -> str:
    parts = []
    if q.fuels:
        parts.append(" or ".join({"solid": "solid fuels", "liquid": "liquid droplets", "gas": "gas flames"}[f]
                                 for f in q.fuels))
    if q.habitat:
        parts.append(HABITATS[q.habitat][3])
    else:
        lo, hi = q.o2
        if lo is not None and hi is not None:
            parts.append(f"oxygen {_num(lo)} to {_num(hi)} %")
        elif lo is not None:
            parts.append(f"oxygen {_num(lo)} % or more")
        elif hi is not None:
            parts.append(f"oxygen {_num(hi)} % or less")
        lo, hi = q.pressure
        if lo is not None and hi is not None:
            parts.append(f"pressure {_num(lo)} to {_num(hi)} kPa ({_psi(lo)} to {_psi(hi)} psi)")
        elif lo is not None:
            parts.append(f"pressure {_num(lo)} kPa ({_psi(lo)} psi) or more")
        elif hi is not None:
            parts.append(f"pressure {_num(hi)} kPa ({_psi(hi)} psi) or less")
    if q.diluents:
        parts.append("diluted with " + " or ".join(q.diluents))
    return f" ({', '.join(parts)})" if parts else ""


def _in(value: float | None, bounds: tuple[float | None, float | None]) -> bool:
    lo, hi = bounds
    if lo is None and hi is None:
        return True
    return value is not None and (lo is None or value >= lo) and (hi is None or value <= hi)


def _base_rows(q: Query, kb: KnowledgeBase) -> list[dict[str, Any]]:
    return [
        t for t in kb.tests
        if (not q.psi_ids or t["investigation_id"] in q.psi_ids)
        and (not q.fuels or t["fuel_phase"] in q.fuels)
        and (not q.diluents or any(d in (t["diluent"] or "") for d in q.diluents))
    ]


def _cite_inv(kb: KnowledgeBase, psi_id: str, label: str, anchor: str, **extra: Any) -> dict[str, Any]:
    inv = kb.by_id[psi_id]
    return {"label": f"{name_with_id(inv)}: {label}", "url": f"{inv['url']}#{anchor}", "kind": "table", **extra}


def table_answer(q: Query, kb: KnowledgeBase, wants_materials: bool = False) -> tuple[str | None, list[dict[str, Any]]]:
    """A plain-code summary of the test rows that match the question, with citations."""
    if not q.has_filters:
        return None, []
    base = _base_rows(q, kb)
    rows = [t for t in base if _in(t["o2_pct"], q.o2) and _in(t["pressure_kpa"], q.pressure)]
    desc = describe_filters(q)
    citations: list[dict[str, Any]] = []
    sentences: list[str] = []
    if rows:
        per: dict[str, int] = {}
        for t in rows:
            per[t["investigation_id"]] = per.get(t["investigation_id"], 0) + 1
        listed = ", ".join(f"{name(kb.by_id[i])} ({n})" for i, n in sorted(per.items(), key=lambda kv: -kv[1]))
        sentences.append(f"In the PSI test tables, {len(rows)} test{'s' if len(rows) != 1 else ''} "
                         f"match{'es' if len(rows) == 1 else ''}{desc}: {listed}.")
        o2 = [t["o2_pct"] for t in rows if t["o2_pct"] is not None]
        kpa = [t["pressure_kpa"] for t in rows if t["pressure_kpa"] is not None]
        flow = [t["flow_cm_s"] for t in rows if t["flow_cm_s"] is not None]
        ranges = []
        if o2:
            ranges.append(f"oxygen {_range(o2, '%')}")
        if kpa:
            ranges.append(f"pressure {_range(kpa, 'kPa')} ({_range([k / KPA_PER_PSI for k in kpa], 'psi')})")
        if flow:
            ranges.append(f"air flow {_range(flow, 'cm/s')}")
        if ranges:
            sentences.append(f"They cover {', '.join(ranges)}.")
        if wants_materials:
            names: dict[str, list[str]] = {}
            for t in rows:
                if t["fuel_material"]:
                    names.setdefault(" ".join(t["fuel_material"].lower().split()), []).append(t["fuel_material"])
            common = sorted(names.values(), key=len, reverse=True)[:8]
            if common:
                listed_names = ", ".join(f"{group[0]} ({len(group)})" for group in common)
                more = f", and {len(names) - len(common)} other descriptions" if len(names) > len(common) else ""
                sentences.append(f"Materials most often named in the tables: {listed_names}{more}.")
        outcomes = {o: sum(t["outcome"] == o for t in rows) for o in ("extinguished", "sustained", "no_ignition")}
        if any(outcomes.values()):
            words = {"extinguished": "the flame went out in {n}", "sustained": "it burned to the end of the test in {n}",
                     "no_ignition": "it did not ignite in {n}"}
            known = [words[o].format(n=n) for o, n in outcomes.items() if n]
            joined = known[0] if len(known) == 1 else ", ".join(known[:-1]) + " and " + known[-1]
            unknown = len(rows) - sum(outcomes.values())
            sentences.append(f"Outcome: {joined}" + (f"; {unknown} have no recorded outcome." if unknown else "."))
        assumed: dict[str, set[str]] = {}
        for t in rows:
            fields = set(transform_tests.assumed_in(t, kb.assumed))
            note = (t["note"] or "").lower()  # the notes still say it where the mappings are not shipped
            if "oxygen not in the source table" in note:
                fields.add("o2_pct")
            if "pressure not in the source table" in note:
                fields.add("pressure_kpa")
            if fields:
                assumed.setdefault(t["investigation_id"], set()).update(fields)
        both = [i for i, f in assumed.items() if {"o2_pct", "pressure_kpa"} <= f]
        pressure_only = [i for i, f in assumed.items() if f == {"pressure_kpa"}]
        if pressure_only:
            sentences.append(f"Pressure for {', '.join(name(kb.by_id[i]) for i in sorted(pressure_only, key=psi_order))} "
                             "is not in the source table; cabin pressure (1 atm) is assumed.")
        if both:
            sentences.append(f"Oxygen and pressure for {', '.join(name(kb.by_id[i]) for i in sorted(both, key=psi_order))} "
                             "are not in the source tables; ISS cabin air (21 % oxygen, 14.7 psi) is assumed.")
        for psi_id, n in sorted(per.items(), key=lambda kv: -kv[1])[:5]:
            citations.append(_cite_inv(kb, psi_id, f"test table, {n} matching row{'s' if n != 1 else ''}", "tests"))
    else:
        sentences.append(f"No test in the PSI test tables matches{desc}.")
        for bound, key, unit, pick in (
            (q.o2[0], "o2_pct", "%", max), (q.o2[1], "o2_pct", "%", min),
            (q.pressure[1], "pressure_kpa", "kPa", min), (q.pressure[0], "pressure_kpa", "kPa", max),
        ):
            if bound is None:
                continue
            others = [t for t in base if t[key] is not None]
            if not others:
                continue
            best = pick(others, key=lambda t: t[key])
            what = {"o2_pct": "oxygen level", "pressure_kpa": "pressure"}[key]
            word = "highest" if pick is max else "lowest"
            scope = scope_words(q, kb)
            value = f"{_num(best[key])} %" if unit == "%" else f"{_num(best[key])} kPa ({_psi(best[key])} psi)"
            sentences.append(f"The {word} {what} tested{scope} is {value} "
                             f"({name(kb.by_id[best['investigation_id']])}, test {best['test_label']}).")
            row = best["source"]["row"]
            citations.append(_cite_inv(kb, best["investigation_id"], f"test {best['test_label']}, table row {row}",
                                       f"row-{row}"))
    # A named investigation with no test rows gets its reason, whatever else matched.
    with_rows = {t["investigation_id"] for t in base}
    for psi_id in q.psi_ids:
        summary = kb.varied.get(psi_id)
        if psi_id not in with_rows and summary and summary.startswith("No test-level data"):
            sentences.append(f"{name(kb.by_id[psi_id])}: {summary}")
    # dedupe citations by url, keep order
    seen: set[str] = set()
    citations = [c for c in citations if not (c["url"] in seen or seen.add(c["url"]))]
    return " ".join(sentences), citations


# --------------------------------------------------------------------------- passages


def doc_title(source_id: str, category: str) -> str:
    title = re.sub(r"^PSI-\d+_", "", source_id)
    if title.startswith(category + "_"):
        title = title[len(category) + 1:]
    title = re.sub(r"\.(pdf|docx?|pptx?)$", "", title, flags=re.IGNORECASE).replace("_", " ").strip()
    return title if len(title) <= 60 else title[:57].rstrip() + "..."


def where(chunk: dict[str, Any]) -> str:
    if chunk.get("kind") == "finding" and chunk["source_id"].endswith("_record"):
        return f"PSI record, {(chunk.get('section') or 'description').replace('_', ' ')}"
    if chunk["category"] == "PSI record":
        return f"PSI record, {(chunk.get('section') or 'description').replace('_', ' ')}"
    title = doc_title(chunk["source_id"], chunk["category"])
    if chunk.get("page") is not None:
        return f"{title}, {'slide' if chunk['source_id'].lower().endswith('.pptx') else 'p.'} {chunk['page']}"
    return f"{title}, {chunk.get('section') or 'text'}"


def best_snippet(text: str, question: str, limit: int = 280) -> str:
    """The run of sentences in `text` that shares the most words with the question."""
    words = {w for w in re.findall(r"[a-z0-9]{3,}", question.lower())} - {"what", "which", "how", "the", "and", "did", "does", "with", "about", "for", "are", "was", "were", "that", "this", "from"}
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    overlap = lambda s: len(words & set(re.findall(r"[a-z0-9]{3,}", s.lower())))
    best, best_score = sentences[0], (-1, 0)
    for i in range(len(sentences)):
        if i and overlap(sentences[i]) == 0:
            continue  # start on a sentence that is about the question
        snippet = sentences[i]
        for j in range(i + 1, len(sentences)):
            if len(snippet) + len(sentences[j]) + 1 > limit:
                break
            snippet += " " + sentences[j]
        score = (overlap(snippet), -len(snippet))
        if score > best_score:
            best, best_score = snippet, score
    return best if len(best) <= limit else best[:limit - 3].rsplit(" ", 1)[0] + "..."


YEAR = re.compile(r"\b(?:19[5-9]\d|20[0-4]\d)\b")
REFERENCE_MARKS = re.compile(r"\bet al\b|\bProc\.|\bJ\. |\bVol\.|\bpp\.|\bdoi\b|Conference|Journal", re.IGNORECASE)


def looks_like_references(text: str) -> bool:
    """Reference lists, acknowledgements and title pages match on words but answer nothing."""
    head = text[:300].lower()
    if "acknowledg" in head or re.search(r"\breferences\b|\bbibliography\b", head):
        return True
    per_1000 = 1000 / max(len(text), 1)
    return (len(YEAR.findall(text)) + len(REFERENCE_MARKS.findall(text))) * per_1000 >= 6


def passages(question: str, q: Query, kb: KnowledgeBase, k: int = MAX_PASSAGES) -> list[dict[str, Any]]:
    if kb.search is None:
        return []
    psi_ids = q.psi_ids or None
    if hasattr(kb.search, "hybrid"):
        found = kb.search.hybrid(question, k=k * 5, psi_ids=psi_ids)
    else:
        found = kb.search.query(question, k=k * 4, psi_ids=psi_ids)
    out, weak, seen, snippets, per_source = [], [], set(), set(), {}
    for chunk in found:
        key = (chunk["source_id"], chunk.get("page"), chunk.get("section"))
        if key in seen or chunk["psi_id"] not in kb.by_id:
            continue
        seen.add(key)
        snippet = best_snippet(chunk["text"], question)
        if snippet in snippets:  # the same text in two documents
            continue
        snippets.add(snippet)
        passage = {**chunk, "snippet": snippet}
        if looks_like_references(chunk["text"]) or PLANNING.search(chunk["source_id"]):
            weak.append(passage)
        elif per_source.get(chunk["source_id"], 0) < PASSAGES_PER_SOURCE:
            per_source[chunk["source_id"]] = per_source.get(chunk["source_id"], 0) + 1
            out.append(passage)
    return (out + weak)[:k]  # planning documents and reference lists only fill the gaps


def key_findings(question: str, q: Query, kb: KnowledgeBase, k: int = MAX_FINDINGS) -> list[dict[str, Any]]:
    """The findings from NASA's reports that best match the question, as passage-like dicts."""
    pool = [f for f in kb.findings if f["investigation_id"] in kb.by_id
            and (not q.psi_ids or f["investigation_id"] in q.psi_ids)]
    if not pool:
        return []
    kb.prepare_findings()
    keyword = kb._finding_keywords.scores(question)
    meaning = None
    if kb._finding_vectors is not None:
        import numpy as np

        meaning = kb._finding_vectors @ np.array(kb.search.embed([question])[0], dtype=np.float32)
    index_of = {id(f): i for i, f in enumerate(kb.findings)}
    scored = []
    for f in pool:
        i = index_of[id(f)]
        cos = float(meaning[i]) if meaning is not None else 0.0
        kw = float(keyword[i])
        # Without a named experiment, a finding must really be about the question.
        if not q.psi_ids and not (cos >= 0.45 or (kw > 0 and cos >= 0.25) or (meaning is None and kw > 2)):
            continue
        scored.append((cos + 0.05 * kw, f))
    scored.sort(key=lambda s: -s[0])
    out = []
    for _, f in scored[:k]:
        out.append({
            "id": f["url"], "source_id": f["source_id"], "psi_id": f["investigation_id"],
            "category": "Key finding", "page": f["page"], "section": f.get("section"),
            "text": f"{f['finding']} \u201c{f['quote']}\u201d", "snippet": f["finding"],
            "finding": f["finding"], "quote": f["quote"], "kind": "finding",
        })
    return out


def passage_citation(kb: KnowledgeBase, chunk: dict[str, Any], n: int) -> dict[str, Any]:
    inv = kb.by_id[chunk["psi_id"]]
    kind = "finding" if chunk.get("kind") == "finding" else "document"
    citation = {"label": f"{name_with_id(inv)}: {where(chunk)}", "url": f"{inv['url']}#source-{n}",
                "kind": kind, "snippet": chunk.get("quote") or chunk["snippet"]}
    if chunk.get("page") is not None:
        citation["page"] = chunk["page"]
    return citation


# --------------------------------------------------------------------------- answers


def retrieval_answer(table_text: str | None, found: list[dict[str, Any]], kb: KnowledgeBase) -> str:
    results = [p for p in found if p.get("kind") == "finding"]
    documents = [p for p in found if p.get("kind") != "finding"]
    parts = []
    if results:
        parts.append(" ".join(f"{p['finding'].rstrip('.')} ({name(kb.by_id[p['psi_id']])}, {where(p)})."
                              for p in results[:PASSAGES_IN_TEXT]))
    if table_text:
        parts.append(table_text)
    if documents and len(results) < 2:
        quoted = "; ".join(
            f"\u201c{p['snippet']}\u201d ({name(kb.by_id[p['psi_id']])}, {where(p)})" for p in documents[:PASSAGES_IN_TEXT]
        )
        parts.append(f"From the documents: {quoted}.")
    if not parts:
        return ("I could not find anything about this in the PSI combustion data. Try naming an experiment "
                "(for example FLEX or BASS-II) or a condition such as an oxygen level or a pressure.")
    return " ".join(parts)


def partial_gravity_answer(question: str, kb: KnowledgeBase) -> dict[str, Any]:
    q = Query(psi_ids=["PSI-22"] if "PSI-22" in kb.by_id else [])
    found = passages(question, q, kb, k=2)
    text = (
        "PSI has no combustion tests in partial gravity (the Moon is about 0.17 g, Mars about 0.38 g). "
        "Every PSI combustion test ran in microgravity, on the ISS or in a Cygnus cargo vehicle, and normal "
        "gravity (1 g) on the ground is the other bound, so flame behaviour in partial gravity lies somewhere "
        "between the two. ACME E-FIELD Flames used an electric field to imitate some effects of gravity on "
        "flames, and two planned experiments, LUCI and FM2, are expected to add partial-gravity data."
    )
    if kb.gravity_paired:
        text += " " + kb.gravity_paired
    citations = [{"label": "Challenge brief: partial-gravity data", "url": BRIEF_URL, "kind": "brief"}]
    if "PSI-22" in kb.by_id:
        citations.append(_cite_inv(kb, "PSI-22", "investigation record", "record", kind="record"))
    if kb.gravity_paired and "PSI-99" in kb.by_id:
        citations.append(_cite_inv(kb, "PSI-99", "test table, microgravity and normal-gravity burn results", "tests"))
    citations += [passage_citation(kb, p, n) for n, p in enumerate(found, start=1)]
    return {"answer": text, "citations": citations, "mode": "no_data"}


class AiReply(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)
    sources_used: list[int]


AI_SYSTEM = """You answer questions about NASA's microgravity combustion experiments for engineers and students,
using only the numbered sources you are given.
Answer the question directly in the first sentence (the result, the yes or no, or the explanation), then
support it with specific results and numbers from the sources.
Prefer sources marked "Key finding": they are results stated in NASA's reports.
The test-table summary (source 0) is authoritative for which tests were run and under what conditions
(oxygen, pressure, flow, fuel): when it lists tests that match the question, say which experiments they
are, and never say such tests do not exist. For conditions, use its values over those in documents.
Do not open with a count of tests unless the question asks how many.
You may compare results across sources when they support it, for example the same experiment at
different oxygen levels or pressures. Never add facts, numbers or experiment names that are not in the
sources, and attach each number only to the experiment and conditions its source is about.
If the sources do not answer the question, say so in one sentence and say what they do cover.
If a source says a value was assumed, say that it was assumed.
Write 2 to 5 plain sentences. Never write source numbers such as [1]; name the experiment or document.
List in sources_used the numbers of the sources you relied on (0 is the test-table summary)."""


def _numbers(text: str) -> list[tuple[float, str]]:
    return [(float(m.group(1).replace(",", "")), (m.group(2) or "").lower()) for m in NUMBER_WITH_UNIT.finditer(text)]


def numbers_supported(answer: str, context: str) -> tuple[bool, list[str]]:
    """Every number in the answer must appear in the context or be a unit conversion of one."""
    allowed = [v for v, _ in _numbers(context)]
    factors = (1.0, KPA_PER_PSI, 1 / KPA_PER_PSI, KPA_PER_ATM, 1 / KPA_PER_ATM, 100.0, 0.01, KPA_PER_MMHG, 1 / KPA_PER_MMHG)
    missing = []
    for value, unit in _numbers(answer):
        if not unit and value.is_integer() and 0 <= value <= 10:
            continue  # small counts such as "2 experiments"
        if not any(abs(value - a * f) <= 0.02 * max(1.0, abs(a * f)) for a in allowed for f in factors):
            missing.append(f"{_num(value)}{unit}")
    return not missing, missing


class Budget:
    """At most `limit` AI calls per calendar day (cache hits are free)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._day = date.today()
        self.used = 0
        self.exhausted = False
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if date.today() != self._day:
                self._day, self.used, self.exhausted = date.today(), 0, False
            if self.exhausted or self.used >= self.limit:
                return False
            self.used += 1
            return True

    def mark_exhausted(self) -> None:
        with self._lock:
            self.exhausted = True


def ai_answer(question: str, table_text: str | None, found: list[dict[str, Any]],
              provider: llm.Provider, budget: Budget,
              kb: KnowledgeBase | None = None) -> tuple[AiReply | None, str]:
    """(reply, reason). reply is None when the AI was not used or its answer failed the checks."""
    sources = []
    if table_text:
        sources.append(f"[0] Test-table summary: {table_text}")
    for n, p in enumerate(found, start=1):
        # The experiment's name with its id: documents filed under one investigation often
        # describe a related one (SAME's papers sit under SAME-R), and a bare id misleads.
        inv = kb.by_id.get(p["psi_id"]) if kb else None
        owner = name_with_id(inv) if inv else p["psi_id"]
        if p.get("kind") == "finding":
            sources.append(f"[{n}] Key finding of {owner}, from {where(p)}: {p['finding']} "
                           f"Quote: \u201c{p['quote']}\u201d")
        else:
            sources.append(f"[{n}] {where(p)}, filed under {owner}: {p['text'][:1200]}")
    # Numbers in an answer may only come from the question or the source texts, not their labels.
    evidence = "\n".join([question, table_text or ""] + [p["text"][:1200] for p in found])
    if not sources:
        return None, "no sources"
    prompt = f"Question: {question}\n\nSources:\n" + "\n\n".join(sources)
    if not llm.is_cached(prompt, AiReply, system=AI_SYSTEM, provider=provider) and not budget.take():
        return None, "daily AI budget used"
    try:
        reply = AiReply.model_validate(llm.complete_json(prompt, AiReply, system=AI_SYSTEM, provider=provider))
    except llm.QuotaError:
        budget.mark_exhausted()
        return None, "AI quota used up"
    except llm.LLMError as exc:
        return None, f"AI error: {exc}"
    ok, missing = numbers_supported(reply.answer, evidence)
    if not ok:
        return None, f"numbers not in the sources: {', '.join(missing)}"
    contradiction = contradicts_table(reply.answer, table_text, kb)
    if contradiction:
        return None, contradiction
    return reply, "ok"


def contradicts_table(answer_text: str, table_text: str | None, kb: KnowledgeBase | None) -> str | None:
    """An answer that ignores the tests the table lists for the question is not used."""
    if not table_text or kb is None:
        return None
    listed = re.match(r"In the PSI test tables, \d+ tests? match(?:es)?[^:]*: ([^.]+)\.", table_text)
    if not listed:
        return None
    names = [re.sub(r"\s*\(\d+\)$", "", part.strip()) for part in listed.group(1).split(",")]
    variants = {v.lower() for n in names for v in (n, n.replace("ACME ", ""))}
    for inv in kb.investigations:  # the full title counts too ("Burning Rate Emulator")
        if name(inv) in names:
            title = re.sub(r"^Advanced Combustion via Microgravity Experiments\s*[-/]?\s*", "", inv["title"])
            variants.add(title.lower())
    if any(v and v in answer_text.lower() for v in variants):
        return None
    return f"answer does not name the tests the table lists ({', '.join(names)})"


def gather(question: str, q: Query, kb: KnowledgeBase) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """Everything an answer may draw on: the table summary, its citations, and document passages."""
    wants_materials = bool(re.search(r"\b(?:materials?|samples?|fuels?)\b", question, re.IGNORECASE))
    table_text, table_cites = table_answer(q, kb, wants_materials)
    return table_text, table_cites, key_findings(question, q, kb) + passages(question, q, kb)


def tidy(text: str) -> str:
    """Remove source labels and numbers ("[2]") that a model sometimes copies into its answer."""
    text = re.sub(r"^\s*(?:\[0\]\s*)?test-table summary:\s*", "", text, flags=re.IGNORECASE)
    one = r"\[\d+(?:\s*(?:,|and|-|–)\s*\d+)*\]"
    text = re.sub(rf"\s*{one}(?:\s*(?:,|and|-|–)\s*{one})*", "", text)
    return re.sub(r"\s+([.,;:])", r"\1", text).strip()


def answer(question: str, kb: KnowledgeBase, provider: llm.Provider | None = None,
           budget: Budget | None = None) -> dict[str, Any]:
    """The reply for POST /ask: {answer, citations, mode, note?}."""
    demo = kb.demo.get(normalise(question))
    if demo:
        return {"answer": demo["answer"], "citations": demo["citations"], "mode": "demo"}
    q = read_question(question, kb)
    if q.partial_gravity:
        return partial_gravity_answer(question, kb)

    table_text, table_cites, found = gather(question, q, kb)
    doc_cites = [passage_citation(kb, p, n) for n, p in enumerate(found, start=1)]
    fallback = {
        "answer": retrieval_answer(table_text, found, kb),
        "citations": table_cites + doc_cites[:PASSAGES_IN_TEXT] + doc_cites[PASSAGES_IN_TEXT:],
        "mode": "retrieval",
    }
    if provider is None or budget is None:
        return fallback
    reply, reason = ai_answer(question, table_text, found, provider, budget, kb)
    if reply is None:
        return {**fallback, "note": reason}
    used = [n for n in dict.fromkeys(reply.sources_used) if (n == 0 and table_text) or 1 <= n <= len(found)]
    citations = (table_cites if 0 in used or not used else []) + [doc_cites[n - 1] for n in used if n >= 1]
    return {"answer": tidy(reply.answer), "citations": citations or fallback["citations"], "mode": "ai"}


# --------------------------------------------------------------------------- demo answers

DEMO_QUESTIONS = [
    # the three example buttons in app/src/views/Ask.tsx
    "Which tests burned solid fuel above 30% oxygen?",
    "What did FLEX find about carbon dioxide as a suppressant?",
    "How does air flow speed change flame spread in BASS-II?",
    "Has any material been burned at lunar-habitat conditions (30% oxygen, 10.2 psi)?",
    "What oxygen levels and pressures did FLEX test?",
    "What happens to flames in partial gravity on the Moon?",
    "Which experiments studied smoke detection?",
    "What is a cool flame and which experiments studied it?",
    "How did Saffire test fire spread on large samples?",
    "Which solid materials were tested, and at what oxygen levels?",
]


def run_demo_answers(args: Any) -> int:
    """Answer the demo questions once (AI if available) and save them for the server to serve."""
    from pipeline.search import SearchIndex

    kb = KnowledgeBase.load(search=SearchIndex.load())
    kb.demo = {}
    try:
        provider: llm.Provider | None = llm.get_provider()
    except llm.LLMError as exc:
        print(f"demo-answers: no AI ({exc}); saving retrieval answers")
        provider = None
    budget = Budget(len(DEMO_QUESTIONS))
    saved = []
    for question in DEMO_QUESTIONS:
        reply = answer(question, kb, provider, budget)
        saved.append({"question": question, "answer": reply["answer"], "citations": reply["citations"],
                      "built_as": reply["mode"], **({"note": reply["note"]} if reply.get("note") else {})})
        print(f"  {reply['mode']:9} {question}" + (f"  ({reply['note']})" if reply.get("note") else ""))
    DEMO_ANSWERS.parent.mkdir(parents=True, exist_ok=True)
    DEMO_ANSWERS.write_text(json.dumps(saved, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(saved)} answers to {DEMO_ANSWERS.relative_to(config.REPO_ROOT)}")
    return 0
