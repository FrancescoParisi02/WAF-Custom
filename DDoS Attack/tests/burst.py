"""Burst load test module (fixed: proper PoW redirect classification + rich detail logging).

Sends a high volume of non-JS HTTP GET requests to a single target using a
thread pool. Each request is classified via `classify_burst` and results are
logged with CsvLogger as summary and, optionally, per-request detail rows.

Key fixes:
- 3xx with Location pointing to /pow => class='blocked'
- Always populate requested_url / redirect_url / landed_url / url_chain
- Always increment counters consistently (sent == ok+blocked+errors)
"""

from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

import requests

from utils.csvlog import CsvLogger
from utils.classify import classify_burst, ClassifyConfig

UA = "LoadTestBurst/1.0"


def run(
    target: str,
    threads: int,
    reqs_per_thread: int,
    results_dir: Path,
    with_detail: bool = False,
    timeout_s: float = 8.0,
    note: str = "",
):
    """Execute a volumetric burst against `target` (no JS, no redirect follow)."""
    test_name = "burst"
    logger = CsvLogger(results_dir, test_name, with_detail)

    # Aggregates
    sent = 0
    passed = 0
    blocked = 0
    errors = 0
    lat_sum_ms = 0
    lat_n = 0

    cfg = ClassifyConfig(pow_path="/pow")  # make PoW path explicit/configurable

    def one_call(worker_id: int, iteration: int) -> Dict[str, Any]:
        """Perform a single GET attempt and return a detail row payload."""
        t0 = time.perf_counter()

        status_code = None
        cls = "other"
        loc = ""
        requested_url = target
        redirect_url = ""
        landed_url = target  # we do NOT follow redirects in burst
        is_redirect = False
        err = ""
        reason = ""

        try:
            r = requests.get(
                target,
                headers={"User-Agent": UA, "Accept": "*/*"},
                timeout=timeout_s,
                allow_redirects=False,  # critical: measure the door slam
            )
            status_code = r.status_code
            # requests sets .is_redirect True for 3xx with Location
            is_redirect = bool(getattr(r, "is_redirect", False)) or (300 <= status_code < 400)
            loc = r.headers.get("Location", "") or ""
            redirect_url = loc
            # Note: since we don't follow, landed_url is the original target
            cls = classify_burst(r, cfg)

        except requests.exceptions.RequestException as e:
            cls = "error"
            reason = type(e).__name__
            err = f"{type(e).__name__}: {e}"
        except Exception as e:
            cls = "error"
            reason = type(e).__name__
            err = f"{type(e).__name__}: {e}"

        lat_ms = int((time.perf_counter() - t0) * 1000)

        # Build a readable chain (requested -> redirect if present)
        if is_redirect and redirect_url:
            url_chain = f"{requested_url} -> {redirect_url}"
        else:
            url_chain = requested_url

        return {
            "ts_iso": datetime.utcnow().isoformat(),
            "test": test_name,
            "worker_id": worker_id,
            "iteration": iteration,
            "status_code": status_code if status_code is not None else "",
            "class": cls,
            "latency_ms": lat_ms,
            "is_redirect": is_redirect,
            "requested_url": requested_url,
            "redirect_url": redirect_url,
            "landed_url": landed_url,
            "url_chain": url_chain,
            "location": loc,
            "reason": reason,
            "error": err,
        }

    # Fire the pool
    with ThreadPoolExecutor(max_workers=threads) as ex:
        futures = []
        for w in range(threads):
            for i in range(reqs_per_thread):
                futures.append(ex.submit(one_call, w, i))

        for fut in as_completed(futures):
            row = fut.result()
            sent += 1
            lat_sum_ms += row["latency_ms"]
            lat_n += 1

            c = row["class"]
            if c == "ok":
                passed += 1
            elif c == "blocked":
                blocked += 1
            elif c == "error":
                errors += 1
            # 'other' exists but shouldn't appear now; if it does, we prefer to see it in detail CSV

            if with_detail:
                logger.write_detail(row)

    # Consistency guard (soft)
    if sent != (passed + blocked + errors):
        delta = sent - (passed + blocked + errors)
        print(f"[{test_name}][warn] counters mismatch by {delta} (classification produced 'other'?)")

    avg_ms = round((lat_sum_ms / lat_n), 2) if lat_n else 0.0
    pct_pass = round((100.0 * passed / sent), 2) if sent else 0.0
    pct_block = round((100.0 * blocked / sent), 2) if sent else 0.0
    pct_err = round((100.0 * errors / sent), 2) if sent else 0.0

    logger.write_summary({
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "which": test_name,
        "requests_sent": sent,
        "passed": passed,
        "blocked": blocked,
        "errors": errors,
        "avg_latency_ms": avg_ms,
        "pct_passed": pct_pass,
        "pct_blocked": pct_block,
        "pct_errors": pct_err,
        "note": note or f"threads={threads} reqs={reqs_per_thread} allow_redirects=False timeout={timeout_s}s",
    })
    logger.close()

    # Optional pretty print for console users
    print(f"[{test_name}][summary] sent={sent} ok={passed} blocked={blocked} errors={errors} avg_latency_ms={avg_ms}")
