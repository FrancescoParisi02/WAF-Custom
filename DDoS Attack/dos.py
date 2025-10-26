#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mixed load tester:
  - burst: volumetric burst of GETs (no redirect follow)
  - simple: many simple HTTP clients that do not execute JS (fail PoW)
  - solver: small pool of headless browsers that execute JS and wait for PoW to finish

Comments are in English (user preference).
"""

from typing import Optional

import argparse
import threading
import time
import csv
import requests
import json
import os
import tempfile

from pathlib import Path
from datetime import datetime
from collections import Counter

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import WebDriverException

from threading import Semaphore

# -------------------------
# CONFIG / TUNABLES
# -------------------------
TARGET_URL = "http://d2ch-eccv.dii.univpm.it/"
CSV_LOG = True
DEBUG_PRINT_FIRST = 5
RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
SOLVER_DEBUG_DIR = RESULTS_DIR / "solver_debug"
SOLVER_DEBUG_DIR.mkdir(parents=True, exist_ok=True)

def mk_metrics_container():
    return {"total": 0, "ok_200": 0, "blocked": 0, "errors": 0, "other_status": Counter()}

def is_blocked_response_text(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return (
        ("proof-of-work" in t) or
        ("proof of work" in t) or
        ("challenge" in t) or
        ("calculating nonce" in t) or
        ("submitting result" in t) or
        ("solve" in t and "javascript" in t)
    )

def print_metrics(name: str, m: dict):
    total = m["total"]
    if total == 0:
        print(f"[{name}] no requests recorded.")
        return
    ok = m["ok_200"]
    blocked = m["blocked"]
    errs = m["errors"]
    print(f"\n[{name}] SUMMARY")
    print(f"  total: {total}")
    print(f"  200 / total   : {ok}  ({ok/total*100:.2f}%)")
    print(f"  blocked / total: {blocked}  ({blocked/total*100:.2f}%)")
    print(f"  errors / total: {errs}  ({errs/total*100:.2f}%)")
    if m["other_status"]:
        print("  other status counts:")
        for s, c in m["other_status"].items():
            print(f"    {s}: {c}")

def write_csv_summary(base_filename: str, metrics: dict) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS_DIR / f"{base_filename}_{ts}.csv"
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["total", "200", "blocked", "errors"])
        w.writerow([metrics["total"], metrics["ok_200"], metrics["blocked"], metrics["errors"]])
    return str(out_path)

def classify_response(r: requests.Response) -> str:
    try:
        if getattr(r, "is_redirect", False):
            loc = r.headers.get("Location", "")
            if "/pow" in loc:
                return "blocked"
        final_url = getattr(r, "url", "") or ""
        if "/pow" in final_url:
            return "blocked"
        if r.status_code in (401, 403, 429):
            return "blocked"
        if r.status_code == 200:
            if is_blocked_response_text(r.text):
                return "blocked"
            return "ok"
        return "other"
    except Exception:
        return "other"

# -------------------------
# Test 1: volumetric_burst
# -------------------------
def volumetric_burst(target: str, num_threads: int = 200, requests_per_thread: int = 50, debug: bool = False):
    print(f"[volumetric_burst] threads={num_threads}, req/thread={requests_per_thread}")
    metrics = mk_metrics_container()
    lock = threading.Lock()
    debug_counter = {"printed": 0}

    def worker(tid: int):
        for i in range(requests_per_thread):
            try:
                r = requests.get(target, timeout=8, allow_redirects=False)
                cls = classify_response(r)
                with lock:
                    metrics["total"] += 1
                    if cls == "ok":
                        metrics["ok_200"] += 1
                    elif cls == "blocked":
                        metrics["blocked"] += 1
                    else:
                        metrics["other_status"][r.status_code] += 1
                if debug and debug_counter["printed"] < DEBUG_PRINT_FIRST:
                    with lock:
                        print(f"[dbg][burst][tid={tid}] status={r.status_code} loc={r.headers.get('Location')} url={r.url}")
                        debug_counter["printed"] += 1
            except Exception as e:
                with lock:
                    metrics["total"] += 1
                    metrics["errors"] += 1
                if debug and debug_counter["printed"] < DEBUG_PRINT_FIRST:
                    with lock:
                        print(f"[dbg][burst][tid={tid}] exception: {e}")
                        debug_counter["printed"] += 1
            if i % 10 == 0:
                time.sleep(0)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    start = time.time()
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("[volumetric_burst] interrupted by user.")
    end = time.time()
    print(f"[volumetric_burst] elapsed {end-start:.2f}s")
    print_metrics("volumetric_burst", metrics)
    if CSV_LOG:
        path = write_csv_summary("volumetric_burst_summary", metrics)
        print(f"  CSV -> {path}")

# -------------------------------------
# Test 2: simple_unable_to_solve
# -------------------------------------
def simple_unable_to_solve(target: str, num_clients: int = 100, iterations: int = 5, delay_between: float = 0.5, debug: bool = False):
    print(f"[simple_unable_to_solve] clients={num_clients} iter={iterations} delay={delay_between}s")
    metrics = mk_metrics_container()
    lock = threading.Lock()
    debug_counter = {"printed": 0}

    def simple_client(cid: int):
        for i in range(iterations):
            try:
                s = requests.Session()
                s.headers.update({"User-Agent": "SimpleBot/1.0"})
                r = s.get(target, timeout=12, allow_redirects=True)
                cls = classify_response(r)
                with lock:
                    metrics["total"] += 1
                    if cls == "ok":
                        metrics["ok_200"] += 1
                    elif cls == "blocked":
                        metrics["blocked"] += 1
                    else:
                        metrics["other_status"][r.status_code] += 1
                if debug and debug_counter["printed"] < DEBUG_PRINT_FIRST:
                    with lock:
                        print(f"[dbg][simple][cid={cid}] status={r.status_code} final_url={r.url} history={[h.status_code for h in r.history]} cookies={s.cookies.get_dict()}")
                        debug_counter["printed"] += 1
                s.cookies.clear()
            except Exception as e:
                with lock:
                    metrics["total"] += 1
                    metrics["errors"] += 1
                if debug and debug_counter["printed"] < DEBUG_PRINT_FIRST:
                    with lock:
                        print(f"[dbg][simple][cid={cid}] exception: {e}")
                        debug_counter["printed"] += 1
            time.sleep(delay_between)

    threads = [threading.Thread(target=simple_client, args=(i,)) for i in range(num_clients)]
    try:
        for t in threads:
            t.start()
            time.sleep(0.01)
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("[simple_unable_to_solve] interrupted by user.")

    print_metrics("simple_unable_to_solve", metrics)
    if CSV_LOG:
        path = write_csv_summary("simple_unable_to_solve_summary", metrics)
        print(f"  CSV -> {path}")

# -------------------------------------
# Test 3: selenium_solver
# -------------------------------------
def selenium_solver(
    target: str,
    browser_clients: int = 4,
    iterations: int = 3,
    wait_for_solve_s: float = 12.0,
    max_concurrent_browsers: int = 2,
    debug: bool = False,
    chrome_binary: Optional[str] = None,
):
    print(f"[selenium_solver] clients={browser_clients} max_concurrent={max_concurrent_browsers} iter={iterations} wait={wait_for_solve_s}s")
    metrics = mk_metrics_container()
    lock = threading.Lock()
    sem = Semaphore(max_concurrent_browsers)
    debug_counter = {"printed": 0}

    def make_driver():
        opts = Options()
        # Commenta la prossima riga per test non-headless
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,800")

        # profilo isolato per ogni worker
        profile_dir = os.path.join(
            tempfile.gettempdir(),
            f"chromium-profile-{os.getpid()}-{int(time.time()*1000)}"
        )
        opts.add_argument(f"--user-data-dir={profile_dir}")

        # bypass proxy di sistema (utile se l'ambiente ha proxy)
        opts.add_argument("--proxy-server=direct://")
        opts.add_argument("--proxy-bypass-list=*")

        if chrome_binary:
            opts.binary_location = chrome_binary

        # log di chromedriver
        log_path = SOLVER_DEBUG_DIR / f"chromedriver_{os.getpid()}_{int(time.time())}.log"
        try:
            log_fh = open(str(log_path), "a", encoding="utf-8")
        except Exception:
            log_fh = None

        # Service con log_output se supportato
        service = None
        if log_fh is not None:
            try:
                service = Service(log_output=log_fh)
            except TypeError:
                service = Service()
        else:
            service = Service()

        try:
            return webdriver.Chrome(service=service, options=opts)
        except WebDriverException:
            return webdriver.Chrome(options=opts)

    def has_pow_cookie(driver) -> bool:
        try:
            # 1) cookie via API Selenium
            for c in driver.get_cookies():
                if c.get("name") == "pow-shield":
                    return True
            # 2) fallback: document.cookie
            dc = driver.execute_script("return document.cookie") or ""
            return "pow-shield=" in dc
        except Exception:
            return False

    def browser_worker(bid: int):
        nonlocal sem
        with sem:
            try:
                driver = make_driver()
            except Exception as e:
                print(f"[solver-{bid}] CHROME START ERROR: {e}")
                with lock:
                    metrics["errors"] += iterations
                    metrics["total"] += iterations
                return

            for it in range(iterations):
                solved = False
                cur_url = ""
                tsnow = int(time.time())
                try:
                    driver.set_page_load_timeout(60)
                    driver.get(target)
                    initial_saved = False
                    if debug and debug_counter["printed"] < DEBUG_PRINT_FIRST:
                        try:
                            cur0 = driver.current_url or ""
                            if "/pow" in cur0:
                                fname_pow = SOLVER_DEBUG_DIR / f"solver_{bid}_iter{it}_{tsnow}_pow"
                                with open(str(fname_pow) + ".html", "w", encoding="utf-8") as f:
                                    f.write(driver.page_source or "")
                                try:
                                    driver.save_screenshot(str(fname_pow) + ".png")
                                except Exception:
                                    pass
                                try:
                                    with open(str(fname_pow) + ".cookies.json", "w", encoding="utf-8") as f:
                                        json.dump(driver.get_cookies(), f, indent=2)
                                except Exception:
                                    pass
                                try:
                                    meta_pow = str(fname_pow) + ".meta.txt"
                                    nav_wd = None
                                    doc_cookie = None
                                    try:
                                        nav_wd = driver.execute_script("return navigator.webdriver")
                                    except Exception:
                                        pass
                                    try:
                                        doc_cookie = driver.execute_script("return document.cookie")
                                    except Exception:
                                        doc_cookie = None
                                    with open(meta_pow, "w", encoding="utf-8") as mf:
                                        mf.write(f"cur_url={cur0}\n")
                                        mf.write(f"navigator.webdriver={nav_wd}\n")
                                        mf.write(f"document.cookie={doc_cookie}\n")
                                        mf.write("stage=pow\n")
                                except Exception:
                                    pass
                                initial_saved = True
                                with lock:
                                    debug_counter["printed"] += 1
                        except Exception:
                            pass

                    waited = 0.0
                    poll = 0.5
                    reason = "timeout"

                    while waited < wait_for_solve_s:
                        try:
                            cur_url = driver.current_url or ""
                            src = driver.page_source or ""
                        except Exception:
                            cur_url, src = "", ""

                        # Segnale forte: cookie presente
                        if has_pow_cookie(driver):
                            solved = True
                            reason = "cookie_present"
                            break

                        # Segnale debole ma accettabile: siamo usciti da /pow e il testo non è la pagina di challenge
                        if "/pow" not in cur_url and not is_blocked_response_text(src):
                            # breve stabilizzazione
                            time.sleep(2.0)
                            # ricontrolla cookie dopo stabilizzazione
                            if has_pow_cookie(driver):
                                solved = True
                                reason = "stable_then_cookie"
                                break
                            solved = True
                            reason = "stable_url"
                            break

                        time.sleep(poll)
                        waited += poll

                    # Verifica “post-solve”: ricarica il target, MA non azzerare se il cookie c’è
                    # --- after detecting solved = True inside the waiting loop ---
                    if solved:
                        # Give the gateway some time to perform the redirect (some PoW pages redirect automatically)
                        post_solve_wait_s = 10.0
                        post_waited = 0.0
                        post_poll = 0.5
                        final_url = driver.current_url or ""
                        final_cookie_val = None

                        # Try to wait for redirect away from /pow
                        while post_waited < post_solve_wait_s:
                            try:
                                cur_after = driver.current_url or ""
                            except Exception:
                                cur_after = ""
                            if "/pow" not in cur_after:
                                final_url = cur_after
                                break
                            time.sleep(post_poll)
                            post_waited += post_poll

                        # If still on /pow, try to explicitly load the target (this can force the gateway to accept cookie)
                        if "/pow" in (driver.current_url or ""):
                            try:
                                driver.get(target)
                                # brief stabilization
                                time.sleep(1.0)
                                final_url = driver.current_url or ""
                            except Exception:
                                pass

                        # Read cookies via Selenium (HttpOnly cookies are visible here)
                        try:
                            cookies = driver.get_cookies()
                            for c in cookies:
                                if c.get("name") == "pow-shield":
                                    final_cookie_val = c.get("value")
                                    break
                        except Exception:
                            cookies = []

                        # Save final debug artifacts (HTML / screenshot / cookies / meta)
                        try:
                            fname_base_final = SOLVER_DEBUG_DIR / f"solver_{bid}_iter{it}_{tsnow}_final"
                            with open(str(fname_base_final) + ".html", "w", encoding="utf-8") as f:
                                f.write(driver.page_source or "")
                            try:
                                driver.save_screenshot(str(fname_base_final) + ".png")
                            except Exception:
                                pass
                            try:
                                with open(str(fname_base_final) + ".cookies.json", "w", encoding="utf-8") as f:
                                    json.dump(cookies, f, indent=2)
                            except Exception:
                                pass
                            try:
                                meta_file_final = str(fname_base_final) + ".meta.txt"
                                nav_wd = None
                                doc_cookie = None
                                try:
                                    nav_wd = driver.execute_script("return navigator.webdriver")
                                except Exception:
                                    pass
                                try:
                                    doc_cookie = driver.execute_script("return document.cookie")
                                except Exception:
                                    doc_cookie = None
                                with open(meta_file_final, "w", encoding="utf-8") as mf:
                                    mf.write(f"cur_url={driver.current_url}\n")
                                    mf.write(f"final_url={final_url}\n")
                                    mf.write(f"navigator.webdriver={nav_wd}\n")
                                    mf.write(f"document.cookie={doc_cookie}\n")
                                    mf.write(f"pow-shield_cookie_value={final_cookie_val}\n")
                            except Exception:
                                pass
                        except Exception as e:
                            # non blocchiamo il flusso per errori di salvataggio
                            print(f"[solver-{bid}] final debug save error: {e}")
                        if debug and (not solved) and (not initial_saved) and debug_counter["printed"] < DEBUG_PRINT_FIRST:
                            fname_base = SOLVER_DEBUG_DIR / f"solver_{bid}_iter{it}_{tsnow}"
                        try:
                            with open(str(fname_base) + ".html", "w", encoding="utf-8") as f:
                                f.write(driver.page_source or "")
                            driver.save_screenshot(str(fname_base) + ".png")
                            try:
                                with open(str(fname_base) + ".cookies.json", "w", encoding="utf-8") as f:
                                    json.dump(driver.get_cookies(), f, indent=2)
                            except Exception:
                                pass
                            try:
                                meta_file = str(fname_base) + ".meta.txt"
                                nav_wd = driver.execute_script("return navigator.webdriver")
                                doc_cookie = driver.execute_script("return document.cookie")
                                with open(meta_file, "w", encoding="utf-8") as mf:
                                    mf.write(f"cur_url={driver.current_url}\n")
                                    mf.write(f"navigator.webdriver={nav_wd}\n")
                                    mf.write(f"document.cookie={doc_cookie}\n")
                                    mf.write(f"reason={reason}\n")  # <--- aggiunta
                            except Exception:
                                pass

                        except Exception as e:
                            print(f"[solver-{bid}] debug save error: {e}")
                        with lock:
                            debug_counter["printed"] += 1

                    with lock:
                        metrics["total"] += 1
                        if solved:
                            metrics["ok_200"] += 1
                        else:
                            metrics["blocked"] += 1

                except Exception as e:
                    with lock:
                        metrics["total"] += 1
                        metrics["errors"] += 1
                    if debug and debug_counter["printed"] < DEBUG_PRINT_FIRST:
                        print(f"[dbg][solver-{bid}] iter={it} solved={solved} reason={reason} cur_url={cur_url}")
                        with lock:
                            print(f"[dbg][solver-{bid}] iter={it} exception: {e}")
                            debug_counter["printed"] += 1
                time.sleep(0.2)

            try:
                driver.quit()
            except Exception:
                pass

    threads = [threading.Thread(target=browser_worker, args=(i,)) for i in range(browser_clients)]
    try:
        for t in threads:
            t.start()
            time.sleep(0.05)
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("[selenium_solver] interrupted by user.")

    print_metrics("selenium_solver", metrics)
    if CSV_LOG:
        path = write_csv_summary("selenium_solver_summary", metrics)
        print(f"  CSV -> {path}")

# -------------------------
# Minimal CLI / main
# -------------------------
def main():
    ap = argparse.ArgumentParser(description="Modular load tester: burst | simple | solver")
    ap.add_argument("--target", default=TARGET_URL, help="Target URL (your lab host)")
    ap.add_argument("--which", choices=["burst", "simple", "solver"], required=True, help="Which test to run")
    ap.add_argument("--confirm-owner", action="store_true", help="Confirm you own the target (required)")
    ap.add_argument("--debug", action="store_true", help="Enable debug prints for the first few requests")

    # burst args
    ap.add_argument("--burst-threads", type=int, default=200, help="Number of threads for burst")
    ap.add_argument("--burst-reqs", type=int, default=20, help="Requests per thread for burst")

    # simple args
    ap.add_argument("--simple-clients", type=int, default=100, help="Simple HTTP clients")
    ap.add_argument("--simple-iter", type=int, default=5, help="Requests per simple client")
    ap.add_argument("--simple-delay", type=float, default=0.5, help="Delay between requests for simple clients")

    # solver args
    ap.add_argument("--solver-clients", type=int, default=4, help="Headless browser clients")
    ap.add_argument("--solver-iter", type=int, default=3, help="Iterations per solver client")
    ap.add_argument("--solver-wait", type=float, default=12.0, help="Seconds to wait for PoW to solve")
    ap.add_argument("--solver-max-concurrent", type=int, default=2, help="Max concurrent browsers")
    ap.add_argument("--chrome-binary", default=None, help="Path to chrome/chromium binary if not in PATH")

    args = ap.parse_args()

    if not args.confirm_owner:
        print("Refusing to run: pass --confirm-owner to confirm you own the target host.")
        raise SystemExit(1)

    print("Starting test. Target:", args.target)

    try:
        if args.which == "burst":
            volumetric_burst(args.target, num_threads=args.burst_threads, requests_per_thread=args.burst_reqs, debug=args.debug)
        elif args.which == "simple":
            simple_unable_to_solve(args.target, num_clients=args.simple_clients, iterations=args.simple_iter, delay_between=args.simple_delay, debug=args.debug)
        elif args.which == "solver":
            selenium_solver(args.target, browser_clients=args.solver_clients, iterations=args.solver_iter, wait_for_solve_s=args.solver_wait, max_concurrent_browsers=args.solver_max_concurrent, debug=args.debug, chrome_binary=args.chrome_binary)
    except KeyboardInterrupt:
        print("Interrupted by user. Exiting.")

if __name__ == "__main__":
    main()
