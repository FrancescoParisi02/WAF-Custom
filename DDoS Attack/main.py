from __future__ import annotations
import argparse
from pathlib import Path

from tests import burst as test_burst
from tests import simple as test_simple
from tests import solver as test_solver

DEFAULT_RESULTS_DIR = Path("results")

def parse_args():
    ap = argparse.ArgumentParser(description="LoadTest Suite: burst | simple | solver")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # burst
    ap_b = sub.add_parser("burst", help="Volumetric burst against backend (no JS, no redirect follow)")
    ap_b.add_argument("--target", required=True)
    ap_b.add_argument("--threads", type=int, default=200)
    ap_b.add_argument("--reqs", type=int, default=50)
    ap_b.add_argument("--detail-csv", action="store_true")
    ap_b.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR))

    # simple
    ap_s = sub.add_parser("simple", help="Bots without JS (follow redirects)")
    ap_s.add_argument("--target", required=True)
    ap_s.add_argument("--clients", type=int, default=100)
    ap_s.add_argument("--iter", type=int, default=5)
    ap_s.add_argument("--delay", type=float, default=0.5)
    ap_s.add_argument("--detail-csv", action="store_true")
    ap_s.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR))

    # solver
    ap_v = sub.add_parser("solver", help="Headless browsers that solve PoW")
    ap_v.add_argument("--target", required=True)
    ap_v.add_argument("--browsers", type=int, default=4)
    ap_v.add_argument("--iter", type=int, default=3)
    ap_v.add_argument("--wait", type=float, default=12.0,
                      help="Polling window to check for success signals; NOT a hard cap")
    ap_v.add_argument("--max-concurrent", type=int, default=2)
    ap_v.add_argument("--chrome-binary", type=str, default=None, help="Custom Chromium/Chrome binary path")
    ap_v.add_argument("--debug", action="store_true", help="Verbose debug for solver")
    ap_v.add_argument("--assume-no-pow", action="store_true",
                      help="Treat non-/pow landing without challenge markers as immediate success")
    # NEW flags
    ap_v.add_argument("--hard-timeout-s", type=float, default=0.0,
                      help="Hard per-attempt wall-clock cap; if >0, abort and classify as blocked when exceeded")
    ap_v.add_argument("--strict-success", action="store_true",
                      help="Stricter success: require leaving /pow and no challenge markers; disables no-PoW fast path")
    ap_v.add_argument("--detail-csv", action="store_true")
    ap_v.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR))

    return ap.parse_args()

def main():
    args = parse_args()
    results_dir = Path(args.results_dir)

    if args.cmd == "burst":
        print(f"[burst] target={args.target} threads={args.threads} reqs/thread={args.reqs}")
        test_burst.run(
            args.target, args.threads, args.reqs,
            results_dir, with_detail=args.detail_csv
        )

    elif args.cmd == "simple":
        print(f"[simple] target={args.target} clients={args.clients} iter={args.iter} delay={args.delay}s")
        test_simple.run(
            args.target, args.clients, args.iter, args.delay,
            results_dir, with_detail=args.detail_csv
        )

    elif args.cmd == "solver":
        print(
            f"[solver] target={args.target} browsers={args.browsers} "
            f"iter={args.iter} wait={args.wait}s max_concurrent={args.max_concurrent} "
            f"chrome_binary={args.chrome_binary} debug={args.debug} "
            f"assume_no_pow={args.assume_no_pow} "
            f"hard_timeout_s={args.hard_timeout_s} strict_success={args.strict_success}"
        )
        test_solver.run(
            args.target, args.browsers, args.iter, args.wait, args.max_concurrent,
            results_dir, with_detail=args.detail_csv,
            chrome_binary=args.chrome_binary, debug=args.debug,
            assume_no_pow=args.assume_no_pow,
            hard_timeout_s=args.hard_timeout_s,
            strict_success=args.strict_success
        )

if __name__ == "__main__":
    main()
