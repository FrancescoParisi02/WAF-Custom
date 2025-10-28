# utils/csvlog.py
"""CSV logging utilities for load tests (stable detail schema, aliasing, normalization)."""

from __future__ import annotations
import csv
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Iterable, Optional, List

# ---- Summary schema (stabile) ------------------------------------------------

DEFAULT_SUMMARY_FIELDS: List[str] = [
    "timestamp", "which", "requests_sent", "passed", "blocked", "errors",
    "avg_latency_ms", "pct_passed", "pct_blocked", "pct_errors", "note",
]

# ---- Detail schema (stabile, superset per burst/simple/solver) ---------------

DEFAULT_DETAIL_FIELDS: List[str] = [
    "ts_iso", "test", "worker_id", "iteration",
    "status_code", "class", "latency_ms", "is_redirect",
    "requested_url", "redirect_url", "landed_url",
    "url_chain", "location",
    "reason", "error",
]

# Alias per compat retrocompatibile: i test che ancora scrivono first_url/final_url
# verranno mappati automaticamente sul nuovo schema.
DETAIL_ALIASES: Dict[str, str] = {
    "first_url": "requested_url",
    "final_url": "landed_url",
    # compat con varianti precedenti
    "start_url": "requested_url",
    "end_url": "landed_url",
    "redirect_location": "redirect_url",
}

class CsvLogger:
    """Simple CSV logger che scrive summary e (opzionalmente) detail.

    Crea file timestampati in `results_dir`:
      - <test_name>_summary_<ts>.csv
      - <test_name>_detail_<ts>.csv (se with_detail=True)

    Comportamento:
      - Summary/Detail hanno schema stabile.
      - write_detail normalizza: applica alias, riempie i mancanti con "",
        ignora extra key non presenti in DEFAULT_DETAIL_FIELDS.
    """

    def __init__(
        self,
        results_dir: Path,
        test_name: str,
        with_detail: bool = False,
        summary_fields: Optional[Iterable[str]] = None,
        detail_fields: Optional[Iterable[str]] = None,
    ):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.test_name = test_name

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")

        # Summary
        self.summary_fields: List[str] = list(summary_fields) if summary_fields else list(DEFAULT_SUMMARY_FIELDS)
        self.summary_path = self.results_dir / f"{test_name}_summary_{ts}.csv"

        # Detail
        self.detail_fields: List[str] = list(detail_fields) if detail_fields else list(DEFAULT_DETAIL_FIELDS)
        self.detail_path: Optional[Path] = None
        self.detail_fh = None
        self.detail_writer: Optional[csv.DictWriter] = None

        if with_detail:
            self.detail_path = self.results_dir / f"{test_name}_detail_{ts}.csv"
            self.detail_fh = self.detail_path.open("w", newline="")
            self.detail_writer = csv.DictWriter(
                self.detail_fh,
                fieldnames=self.detail_fields,
                extrasaction="ignore",  # non esplodere su chiavi extra
            )
            self.detail_writer.writeheader()

    # -- Summary ----------------------------------------------------------------

    def write_summary(self, row: Dict[str, Any], header: Optional[Iterable[str]] = None):
        """Scrive una riga di summary. Header stabile, ignora extra."""
        header_fields = list(header) if header else self.summary_fields
        write_header = not self.summary_path.exists()
        with self.summary_path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header_fields, extrasaction="ignore")
            if write_header:
                w.writeheader()
            # Riempie i campi mancanti con "", ignora extra
            safe = {k: row.get(k, "") for k in header_fields}
            w.writerow(safe)

    # -- Detail -----------------------------------------------------------------

    def _apply_aliases(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Applica alias (first_url->requested_url, ecc.)."""
        if not row:
            return {}
        out = dict(row)
        for old, new in DETAIL_ALIASES.items():
            if old in out and new not in out:
                out[new] = out[old]
        return out

    def write_detail(self, row: Dict[str, Any]):
        """Scrive una riga di detail normalizzata; no-op se detail disabilitato."""
        if not self.detail_writer:
            return
        try:
            # 1) alias retrocompat
            row = self._apply_aliases(row)
            # 2) normalizza: prendi solo i campi noti, riempi mancanti
            safe = {k: row.get(k, "") for k in self.detail_fields}
            self.detail_writer.writerow(safe)
        except Exception as e:
            # Non bloccare il test su un errore di logging
            print(
                f"[CsvLogger] write_detail error for '{self.detail_path}': {type(e).__name__}: {e}",
                file=sys.stderr,
                flush=True,
            )

    # -- Cleanup ----------------------------------------------------------------

    def close(self):
        try:
            if self.detail_fh:
                self.detail_fh.flush()
                self.detail_fh.close()
        except Exception as e:
            print(
                f"[CsvLogger] Error while closing detail CSV '{self.detail_path}': {type(e).__name__}: {e}",
                file=sys.stderr,
                flush=True,
            )
