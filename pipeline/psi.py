"""Client for the public PSI repository API at psi.nasa.gov (no login needed).

Endpoints used:
  GET  /repo/search                  every investigation, Elasticsearch-shaped
  GET  /repo/investigations/<id>     one investigation record
  POST /files/v2                     the full file tree of one investigation
  GET  /studies/<id>/download        a short-lived signed URL for one file
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

BASE_URL = "https://psi.nasa.gov/geode-py/ws"
SITE_URL = "https://psi.nasa.gov/physci/repo/data/investigations"

# PSI-23 and PSI-39 return multi-megabyte records that take over a minute.
TIMEOUT_S = 300
RETRIES = 3


class PSIError(RuntimeError):
    """The PSI API failed or returned something unexpected."""


def _request(url: str, body: dict[str, Any] | None = None) -> bytes:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": "flame-in-freefall-pipeline"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500:  # a 4xx will not get better on retry
                raise PSIError(f"{url} -> HTTP {exc.code}: {exc.read()[:200]!r}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(2 * (attempt + 1))
    raise PSIError(f"{url} failed after {RETRIES} attempts: {last}") from last


def _json(raw: bytes, url: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PSIError(f"{url} did not return JSON: {raw[:200]!r}") from exc


def search_investigations() -> list[dict[str, Any]]:
    """Return the search-index entry (`_source`) of every PSI investigation."""
    query = urllib.parse.urlencode(
        {"term": "", "source": "psi", "type": "investigation", "size": 500}
    )
    url = f"{BASE_URL}/repo/search?{query}"
    hits = _json(_request(url), url)["hits"]
    if hits["total"] > len(hits["hits"]):
        raise PSIError(f"search returned {len(hits['hits'])} of {hits['total']}; add paging")
    return [h["_source"] for h in hits["hits"]]


def get_record(psi_id: str) -> dict[str, Any]:
    url = f"{BASE_URL}/repo/investigations/{psi_id}"
    return _json(_request(url), url)


def get_file_tree(psi_id: str, version: str | int) -> dict[str, Any]:
    """The full file tree. The record's own added/modified lists only cover the last version."""
    url = f"{BASE_URL}/files/v2"
    body = {"folder": "", "fileType": "study", "studyId": psi_id, "version": str(version)}
    return _json(_request(url, body), url)


def signed_url(psi_id: str, file_name: str, version: str | int) -> str:
    """A signed S3 URL for one file. It expires within minutes, so never store it."""
    query = urllib.parse.urlencode(
        {"file": file_name, "version": version, "redirect": "false"},
        quote_via=urllib.parse.quote,
    )
    url = f"{BASE_URL}/studies/{psi_id}/download?{query}"
    out = _request(url).decode("utf-8").strip()
    if not out.startswith("https://"):
        raise PSIError(f"{url} did not return a URL: {out[:200]!r}")
    return out


def iter_files(tree: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (category, file node) for every file in a tree from get_file_tree()."""

    def walk(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
        # Only files carry directory=False; category nodes have no such key.
        if node.get("directory") is False:
            yield node
        else:
            for child in node.get("files") or []:
                yield from walk(child)

    for top in tree.get("files") or []:
        category = top.get("displayName") or top.get("sortingCategory") or ""
        for leaf in walk(top):
            yield leaf.get("sortingCategory") or category, leaf
