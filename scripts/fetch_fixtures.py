"""Fetch licensed fixtures listed in eval/fixtures/MANIFEST.json and verify them.

Only Creative Commons / public-domain sources belong in the manifest. Each entry is
resolved through the Wikimedia Commons API, the live licence must match the manifest,
and the sha256 is recorded on first download. Media is git-ignored.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent / "eval" / "fixtures"
MANIFEST = ROOT / "MANIFEST.json"
MEDIA = ROOT / "media"
API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "Clipforge-fixtures/0.1 (local dev tool)"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def resolve(client: httpx.Client, title: str) -> tuple[str, str]:
    params = {
        "action": "query",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "format": "json",
    }
    r = client.get(API, params=params)
    for attempt in range(6):
        if r.status_code != 429:
            break
        time.sleep(float(r.headers.get("retry-after", 2 * (attempt + 1))))
        r = client.get(API, params=params)
    r.raise_for_status()
    page = next(iter(r.json()["query"]["pages"].values()))
    info = page["imageinfo"][0]
    return info["url"].split("?")[0], info["extmetadata"]["LicenseShortName"]["value"]


def fetch(client: httpx.Client, entry: dict) -> str:
    url, licence = resolve(client, entry["commons_title"])
    if licence != entry["licence"]:
        raise SystemExit(f"licence changed for {entry['file']}: {licence} != {entry['licence']}")
    dest = MEDIA / entry["file"]
    if dest.exists() and entry.get("sha256") == sha256(dest):
        return "cached"
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".partial")
    for attempt in range(8):
        with client.stream("GET", url) as r:
            if r.status_code == 429:
                time.sleep(float(r.headers.get("retry-after", 5 * (attempt + 1))))
                continue
            r.raise_for_status()
            with part.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
            break
    else:
        raise SystemExit(f"rate limited too long fetching {entry['file']}")
    part.rename(dest)
    digest = sha256(dest)
    if entry.get("sha256") and entry["sha256"] != digest:
        raise SystemExit(f"checksum mismatch for {entry['file']}")
    entry["sha256"] = digest
    return "downloaded"


def main() -> int:
    manifest = json.loads(MANIFEST.read_text())
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=120) as client:
        for entry in manifest["fixtures"]:
            print(f"{entry['file']}: {fetch(client, entry)} ({entry['licence']})", flush=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
