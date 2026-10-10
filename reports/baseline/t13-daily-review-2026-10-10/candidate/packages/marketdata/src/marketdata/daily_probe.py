"""Finite HEAD-only availability observations; no downloading, importing or completion credit."""

import tomllib
from datetime import timedelta
from pathlib import Path

import httpx

from .binance import SourceError, archive_url
from .domain import DAY, DataError, json_bytes, sha256, utc


def probe(scheduler, config_path):
    config = tomllib.loads(Path(config_path).read_text())["observation"]
    if not config.get("enabled", False):
        return {"state": "disabled", "requests": 0}
    start, end = utc(config["start_day"]), utc(config["end_day"])
    if (
        any((v.hour, v.minute, v.second, v.microsecond) != (0, 0, 0, 0) for v in (start, end))
        or not DAY <= end - start <= 7 * DAY
    ):
        raise DataError("Probe requires a fixed 1..7 day UTC target window")
    seconds = config.get("interval_seconds", 300)
    if type(seconds) is not int or not 300 <= seconds <= 3600:
        raise DataError("Probe interval must be 300..3600 seconds")
    opens, closes = config.get("opens_kst", "14:30"), config.get("closes_kst", "21:30")
    if not "00:00" <= opens < closes <= "23:59":
        raise DataError("Invalid observation hours")
    keys = [
        k
        for k in scheduler.keys
        if k.market == "linear_perpetual"
        and k.dataset in {"trade_bars", "mark_price_bars", "index_price_bars", "open_interest"}
    ]
    if len(keys) > 4:
        raise DataError("At most four archive keys may be observed")
    config_hash = sha256(json_bytes(config))
    requests = 0
    with scheduler.ledger.exclusive():
        existing = {r["id"]: r for r in scheduler.ledger.rows("daily_probe_targets")}
        day = start
        while day < end:
            local_day = (day + DAY).date().isoformat()
            begin = scheduler.schedule.at(local_day, opens)
            stop = scheduler.schedule.at(local_day, closes)
            for key in keys:
                base = archive_url(key, day, False)
                for url in (base, base + ".CHECKSUM"):
                    identity = sha256(json_bytes([config_hash, url]))
                    row = existing.get(identity) or {
                        "id": identity,
                        "config_hash": config_hash,
                        "url": url,
                        "target_day": day.date().isoformat(),
                        "window_start": begin,
                        "window_end": stop,
                        "next_at": begin,
                        "state": "scheduled",
                        "last_404": None,
                        "first_200": None,
                        "observations": [],
                    }
                    current = scheduler.clock()
                    if row["state"] in {"available", "window_closed"}:
                        continue
                    if current > stop:
                        row.update(state="window_closed", right_censored=True)
                    elif current >= begin and current >= utc(row["next_at"]):
                        # Persist before sending, so a restart cannot immediately repeat the probe.
                        event = {
                            "started_at": current,
                            "method": "HEAD",
                            "status": None,
                            "outcome": "request_outcome_unknown",
                        }
                        row["observations"].append(event)
                        row.update(next_at=current + timedelta(seconds=seconds), state="observing")
                        scheduler.ledger.save("daily_probe_targets", row)
                        try:
                            scheduler.check_cooldown(url)
                            with scheduler.client.stream("HEAD", url) as response:
                                requests += 1
                                event.update(
                                    status=response.status_code,
                                    headers=dict(response.headers),
                                    received_at=scheduler.clock(),
                                    outcome="headers_received",
                                )
                                if response.status_code in {429, 418}:
                                    row["next_at"] = scheduler.cooldown(url, response.headers)
                                elif response.status_code == 404:
                                    row["last_404"] = event["received_at"]
                                elif response.status_code == 200:
                                    row.update(
                                        first_200=event["received_at"],
                                        state="available",
                                        left_censored=row["last_404"] is None,
                                    )
                        except (httpx.HTTPError, SourceError) as exc:
                            event.update(outcome="request_error", error=str(exc))
                    scheduler.ledger.save("daily_probe_targets", row)
            day += DAY
        rows = [
            r
            for r in scheduler.ledger.rows("daily_probe_targets")
            if r["config_hash"] == config_hash
        ]
        return {
            "state": "finished"
            if rows and all(r["state"] in {"available", "window_closed"} for r in rows)
            else "observing",
            "config_hash": config_hash,
            "requests": requests,
            "available_urls": sum(r["state"] == "available" for r in rows),
            "scope_urls": len(rows),
            "data_completion_credit": False,
            "semantics": "observer_path_404_to_200_not_origin_publication_time",
        }
