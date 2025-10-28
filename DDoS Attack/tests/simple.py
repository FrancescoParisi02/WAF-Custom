"""Simple bots load test module.

Simulates 'non-JS' clients that follow HTTP redirects using requests.Session.
Each iteration issues a GET, classifies the outcome via `classify_simple`,
and logs results through CsvLogger (summary and optional detail rows).
"""

from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import requests
from pathlib import Path

from utils.csvlog import CsvLogger
from utils.classify import classify_simple, ClassifyConfig

UA_SIMPLE = "LoadTestSimple/1.0"

def run(
    target: str,
    clients: int,
    iterations: int,
    delay_s: float,
    results_dir: Path,
    with_detail: bool = False,
    timeout_s: float = 12.0,
    note: str = ""
):
    """Execute the 'simple' test with requests-based clients.

    Spawns `clients` concurrent workers; each worker performs `iterations`
    GET requests to `target`, following redirects (no JS execution).
    Responses are classified with `classify_simple` and aggregated into
    summary metrics; optional per-request detail rows are written when
    `with_detail` is True.

    Args:
        target: Absolute URL to hit.
        clients: Number of concurrent simulated clients.
        iterations: Requests performed by each client.
        delay_s: Sleep between iterations for each client.
        results_dir: Directory where CSV files will be written.
        with_detail: If True, log one detail row per request.
        timeout_s: Per-request timeout in seconds.
        note: Free-form note stored in the summary CSV.

    Returns:
        None. Side effects: writes summary/detail CSV via CsvLogger.
    """
    logger = CsvLogger(results_dir, "simple", with_detail)
    metrics = {"total":0, "passed":0, "blocked":0, "errors":0, "lat_sum_ms":0, "lat_n":0}

    def one_client(cid: int):
        """Perform all iterations for a single simulated client and return detail rows."""
        s = requests.Session()
        s.headers.update({"User-Agent": UA_SIMPLE})
        out_rows = []
        for it in range(iterations):
            t0 = time.perf_counter()
            status_code = None
            cls = "other"; is_redirect=False; final_url=""; err=""; loc=""
            try:
                r = s.get(target, timeout=timeout_s, allow_redirects=True)
                status_code = r.status_code
                final_url = getattr(r, "url", "") or ""
                is_redirect = bool(r.history)
                cls = classify_simple(r, ClassifyConfig(mode="waf"))
            except Exception as e:
                err = str(e); cls = "error"
            lat_ms = int((time.perf_counter()-t0)*1000)
            out_rows.append({
                "ts_iso": datetime.utcnow().isoformat(),
                "test": "simple",
                "worker_id": cid,
                "iteration": it,
                "status_code": status_code if status_code is not None else "",
                "class": cls,
                "latency_ms": lat_ms,
                "is_redirect": is_redirect,
                "final_url": final_url,
                "location": loc,
                "error": err,
            })
            time.sleep(delay_s)
        return out_rows

    with ThreadPoolExecutor(max_workers=clients) as ex:
        futures = [ex.submit(one_client, c) for c in range(clients)]
        for fut in as_completed(futures):
            for row in fut.result():
                metrics["total"] += 1
                if row["class"] == "ok":
                    metrics["passed"] += 1
                elif row["class"] == "blocked":
                    metrics["blocked"] += 1
                elif row["class"] == "error":
                    metrics["errors"] += 1
                metrics["lat_sum_ms"] += row["latency_ms"]
                metrics["lat_n"] += 1
                if with_detail:
                    logger.write_detail(row)

    avg_ms = (metrics["lat_sum_ms"] / metrics["lat_n"]) if metrics["lat_n"] else 0.0
    pct_pass = (metrics["passed"] / metrics["total"] * 100.0) if metrics["total"] else 0.0
    pct_block = (metrics["blocked"] / metrics["total"] * 100.0) if metrics["total"] else 0.0
    pct_err = (metrics["errors"] / metrics["total"] * 100.0) if metrics["total"] else 0.0

    logger.write_summary({
        "timestamp": datetime.utcnow().isoformat()+"Z",
        "which": "simple",
        "requests_sent": metrics["total"],
        "passed": metrics["passed"],
        "blocked": metrics["blocked"],
        "errors": metrics["errors"],
        "avg_latency_ms": f"{avg_ms:.2f}",
        "pct_passed": f"{pct_pass:.2f}",
        "pct_blocked": f"{pct_block:.2f}",
        "pct_errors": f"{pct_err:.2f}",
        "note": note,
    })
    logger.close()
