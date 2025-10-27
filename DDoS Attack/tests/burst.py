from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import requests
from pathlib import Path

from utils.csvlog import CsvLogger
from utils.classify import classify_burst, ClassifyConfig

UA = "LoadTestBurst/1.0"

def run(target: str, threads: int, reqs_per_thread: int, results_dir: Path,
        with_detail: bool = False, timeout_s: float = 8.0, note: str = ""):
    logger = CsvLogger(results_dir, "burst", with_detail)
    metrics = {"total": 0, "passed": 0, "blocked": 0, "errors": 0, "lat_sum_ms": 0, "lat_n": 0}

    def one_call(worker_id: int, iteration: int):
        t0 = time.perf_counter()
        status_code = None
        cls = "other"
        is_redirect = False
        loc = ""
        url = ""
        err = ""
        try:
            r = requests.get(target, headers={"User-Agent": UA}, timeout=timeout_s, allow_redirects=False)
            status_code = r.status_code
            is_redirect = bool(getattr(r, "is_redirect", False))
            loc = r.headers.get("Location", "") or ""
            url = getattr(r, "url", "") or ""
            cls = classify_burst(r, ClassifyConfig())
        except Exception as e:
            err = str(e)
            cls = "error"
        lat_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ts_iso": datetime.utcnow().isoformat(),
            "test": "burst",
            "worker_id": worker_id,
            "iteration": iteration,
            "status_code": status_code if status_code is not None else "",
            "class": cls,
            "latency_ms": lat_ms,
            "is_redirect": is_redirect,
            "final_url": url,
            "location": loc,
            "error": err,
        }

    with ThreadPoolExecutor(max_workers=threads) as ex:
        futures = []
        for w in range(threads):
            for i in range(reqs_per_thread):
                futures.append(ex.submit(one_call, w, i))
        for fut in as_completed(futures):
            row = fut.result()
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
        "which": "burst",
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
