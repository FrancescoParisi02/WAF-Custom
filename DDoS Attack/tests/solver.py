from __future__ import annotations
from pathlib import Path
from datetime import datetime
from utils.csvlog import CsvLogger

def run(target: str, browsers: int, iterations: int, wait_s: float, max_concurrent: int,
        results_dir: Path, with_detail: bool = False, note: str = ""):
    logger = CsvLogger(results_dir, "solver", with_detail)
    logger.write_summary({
        "timestamp": datetime.utcnow().isoformat()+"Z",
        "which": "solver",
        "requests_sent": 0,
        "passed": 0,
        "blocked": 0,
        "errors": 0,
        "avg_latency_ms": "0.00",
        "pct_passed": "0.00",
        "pct_blocked": "0.00",
        "pct_errors": "0.00",
        "note": note or "stub",
    })
    logger.close()
