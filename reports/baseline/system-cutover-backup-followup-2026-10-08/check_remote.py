"""Read only the exact public archive URLs left pending by the saved daily run."""
import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent


def inspect(item):
    key, url = item
    assert urllib.parse.urlsplit(url).scheme == "https"
    assert urllib.parse.urlsplit(url).netloc == "data.binance.vision"
    row = {"key": key, "url": url, "checked_at": datetime.now(timezone.utc).isoformat(), "requests": []}
    for method, target in (("HEAD", url), ("HEAD", url + ".CHECKSUM")):
        began = time.monotonic()
        request = urllib.request.Request(target, method=method, headers={"User-Agent": "marketdata-cutover-readiness/1", "Cache-Control": "no-cache"})
        result = {"method": method, "url": target}
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result.update(status=response.status, final_url=response.url, headers=dict(response.headers))
        except urllib.error.HTTPError as exc:
            result.update(status=exc.code, final_url=exc.url, headers=dict(exc.headers))
        except (OSError, urllib.error.URLError) as exc:
            result.update(status=None, error=repr(exc))
        result["elapsed_seconds"] = time.monotonic() - began
        row["requests"].append(result)
    if row["requests"][0]["status"] == 404:
        request = urllib.request.Request(url, headers={"User-Agent": "marketdata-cutover-readiness/1", "Range": "bytes=0-511", "Cache-Control": "no-cache"})
        result = {"method": "GET", "range": "bytes=0-511", "url": url}
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                sample = response.read(512)
                result.update(status=response.status, headers=dict(response.headers), prefix_hex=sample[:8].hex())
        except urllib.error.HTTPError as exc:
            result.update(status=exc.code, headers=dict(exc.headers), error_body=exc.read(4096).decode("utf-8", errors="replace"))
        except (OSError, urllib.error.URLError) as exc:
            result.update(status=None, error=repr(exc))
        row["requests"].append(result)
    statuses = [r["status"] for r in row["requests"]]
    row["classification"] = "available" if statuses[:2] == [200, 200] else (
        "not_available_at_recorded_urls" if statuses == [404, 404, 404] else "needs_review")
    return row


if __name__ == "__main__":
    daily = json.loads((HERE / "daily-latest.json").read_text())
    targets = [(r["key"], x["url"]) for r in daily for x in r.get("results", []) if x.get("state") == "waiting_source"]
    assert len(targets) == 4
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(inspect, targets))
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "read_only": True, "observations": rows}
    (HERE / "remote-availability.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps([{"key": r["key"], "classification": r["classification"], "statuses": [x["status"] for x in r["requests"]]} for r in rows], ensure_ascii=False))
