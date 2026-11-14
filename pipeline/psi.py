"""Client for the public PSI repository API at psi.nasa.gov (no login needed).

Endpoints used:
  GET  /repo/search                  every investigation, Elasticsearch-shaped
  GET  /repo/investigations/<id>     one investigation record
  POST /files/v2                     the full file tree of one investigation
  GET  /studies/<id>/download        a short-lived signed URL for one file
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator

BASE_URL = "https://psi.nasa.gov/geode-py/ws"
SITE_URL = "https://psi.nasa.gov/physci/repo/data/investigations"

# PSI-23 and PSI-39 return multi-megabyte records that take over a minute.
TIMEOUT_S = 300
DOWNLOAD_TIMEOUT_S = 60  # per socket read; a stalled file download should fail fast
RETRIES = 3


class PSIError(RuntimeError):
    """The PSI API failed or returned something unexpected."""


class PSINotFound(PSIError):
    """PSI lists the file but does not have it (HTTP 404). Retrying will not help."""


def _request(url: str, body: dict[str, Any] | None = None, timeout: float = TIMEOUT_S) -> bytes:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": "flame-in-freefall-pipeline"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500:  # a 4xx will not get better on retry
                kind = PSINotFound if exc.code == 404 else PSIError
                raise kind(f"{url} -> HTTP {exc.code}: {exc.read()[:200]!r}") from exc
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


def signed_url(
    psi_id: str, file_name: str, version: str | int, timeout: float = TIMEOUT_S
) -> str:
    """A signed S3 URL for one file. It expires within minutes, so never store it."""
    query = urllib.parse.urlencode(
        {"file": file_name, "version": version, "redirect": "false"},
        quote_via=urllib.parse.quote,
    )
    url = f"{BASE_URL}/studies/{psi_id}/download?{query}"
    out = _request(url, timeout=timeout).decode("utf-8").strip()
    if not out.startswith("https://"):
        raise PSIError(f"{url} did not return a URL: {out[:200]!r}")
    return out


def download(psi_id: str, file_name: str, version: str | int, dest: Path) -> tuple[int, str]:
    """Download one file to `dest`; return (bytes written, sha256).

    The file is written next to `dest` first and renamed when complete, so an
    interrupted run never leaves a half-written file under the final name.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            digest, size = hashlib.sha256(), 0
            link = signed_url(psi_id, file_name, version, timeout=DOWNLOAD_TIMEOUT_S)
            with urllib.request.urlopen(link, timeout=DOWNLOAD_TIMEOUT_S) as resp, part.open(
                "wb"
            ) as fh:
                expected = resp.headers.get("Content-Length")
                while chunk := resp.read(1 << 20):
                    fh.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if expected is not None and size != int(expected):
                raise PSIError(f"connection dropped after {size} of {expected} bytes")
            part.replace(dest)
            return size, digest.hexdigest()
        except PSINotFound:
            part.unlink(missing_ok=True)
            raise
        except urllib.error.HTTPError as exc:
            part.unlink(missing_ok=True)
            if exc.code == 404:
                raise PSINotFound(f"{psi_id}/{file_name}: listed by PSI but missing (HTTP 404)") from exc
            last = exc
            time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError, PSIError) as exc:
            last = exc
            part.unlink(missing_ok=True)
            time.sleep(2 * (attempt + 1))
    raise PSIError(f"download of {psi_id}/{file_name} failed after {RETRIES} attempts: {last}") from last


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
