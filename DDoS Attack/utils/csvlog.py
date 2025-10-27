from __future__ import annotations
import csv
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Iterable

DEFAULT_SUMMARY_FIELDS = [
    "timestamp","which","requests_sent","passed","blocked","errors",
    "avg_latency_ms","pct_passed","pct_blocked","pct_errors","note"
]

DEFAULT_DETAIL_FIELDS = [
    "ts_iso","test","worker_id","iteration","status_code","class",
    "latency_ms","is_redirect","final_url","location","error"
]

class CsvLogger:
    def __init__(self, results_dir: Path, test_name: str, with_detail: bool = False):
        self.results_dir = results_dir
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.test_name = test_name
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.summary_path = self.results_dir / f"{test_name}_summary_{ts}.csv"
        self.detail_path = None
        self.detail_fh = None
        self.detail_writer = None
        if with_detail:
            self.detail_path = self.results_dir / f"{test_name}_detail_{ts}.csv"
            self.detail_fh = self.detail_path.open("w", newline="")
            self.detail_writer = csv.DictWriter(self.detail_fh, fieldnames=DEFAULT_DETAIL_FIELDS)
            self.detail_writer.writeheader()

    def write_summary(self, row: Dict[str, Any], header: Iterable[str] = DEFAULT_SUMMARY_FIELDS):
        write_header = not self.summary_path.exists()
        with self.summary_path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(header))
            if write_header:
                w.writeheader()
            w.writerow(row)

    def write_detail(self, row: Dict[str, Any]):
        if not self.detail_writer:
            return
        self.detail_writer.writerow(row)

    def close(self):
        try:
            if self.detail_fh:
                self.detail_fh.flush()
                self.detail_fh.close()
        except Exception:
            pass
