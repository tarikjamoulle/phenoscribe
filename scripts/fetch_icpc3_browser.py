"""Fetch ICPC-3 classes and their ICPC-2 codes from the ICPC-3 browser.

The ICPC-3 Foundation browser (https://browser.icpc-3.info/) gives, for each
ICPC-3 class, its title and the ICPC-2 code(s) it converts to. The browser
answers HTTP 429 to fast clients, so this script sends one request per
`--delay` seconds and keeps a local cache: a second run fetches only the
codes that are not in the cache yet.

The cache (data/icpc/icpc3_browser.json) holds ICPC-3 titles, which belong to
the ICPC-3 Foundation, so it stays out of git. build_icpc_table.py reads it.

Usage:
    uv run python scripts/fetch_icpc3_browser.py AS04 RS02 ...
    uv run python scripts/fetch_icpc3_browser.py --search "abdominal pain"
"""

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BROWSER_URL = "https://browser.icpc-3.info/browse.php"
CACHE_PATH = Path("data/icpc/icpc3_browser.json")


class Browser:
    def __init__(self, delay: float = 1.5):
        self.delay = delay
        self._last = 0.0

    def get(self, operation: str, **params):
        """One GET to browse.php. Waits `delay` between calls, backs off on 429."""
        query = urllib.parse.urlencode({"operation": operation, **params})
        for attempt in range(5):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                with urllib.request.urlopen(f"{BROWSER_URL}?{query}", timeout=30) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    raise
                time.sleep(30 * (attempt + 1))
        raise RuntimeError(f"ICPC-3 browser still answers 429 for {query}")

    def lookup(self, code: str) -> dict:
        """Title and ICPC-2 codes of one ICPC-3 class, or found=False."""
        found = self.get("searchCode", str=code)["found"]
        if not found:
            return {"found": False}
        rubrics = self.get("getRubrics", id=found[0])
        title = next((r["label"] for r in rubrics if r["kind"] == "preferred"), rubrics[0]["label"])
        icpc2 = [_strip_tags(r["label"]) for r in rubrics if r["kind"] == "icpc-2"]
        return {"found": True, "code": rubrics[0]["code"], "title": title.strip(), "icpc2": icpc2}

    def search(self, text: str) -> list[tuple[str, str]]:
        """(code, title) of the classes whose rubrics match `text`."""
        children = self.get("search", str=text)["children"]
        return [(c["code"], c["text"]) for c in children if c.get("code")]


def _strip_tags(label: str) -> str:
    return re.sub(r"<[^>]+>", "", label).strip()


def load_cache(path: Path = CACHE_PATH) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def fetch_codes(codes: list[str], browser: Browser, path: Path = CACHE_PATH) -> dict:
    """Add the missing codes to the cache file. Saves after each code."""
    cache = load_cache(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for code in codes:
        if code in cache and "error" not in cache[code]:
            continue
        cache[code] = browser.lookup(code)
        path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
        print(code, cache[code])
    return cache


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("codes", nargs="*", help="ICPC-3 codes to fetch")
    parser.add_argument("--search", action="append", default=[], help="text to search")
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    args = parser.parse_args()

    browser = Browser(args.delay)
    for text in args.search:
        print(f"## {text}")
        for code, title in browser.search(text)[:8]:
            print(f"   {code}  {title}")
    if args.codes:
        fetch_codes(args.codes, browser)
