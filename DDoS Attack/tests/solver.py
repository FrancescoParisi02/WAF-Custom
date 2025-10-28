"""Selenium-based solver load test (patched: requested/redirect/landed URLs + no-PoW fast path).

- Adds explicit CSV fields: requested_url, redirect_url (/pow), landed_url (final).
- url_chain always starts with requested_url.
- is_redirect computed deterministically (requested_url != redirect_url).
- reason separated from error (error contains only real exceptions).
- Keeps fast-path behavior and cookie normalization.
- NEW: no-PoW detection & short-circuit success (with optional assume_no_pow flag).
"""

from __future__ import annotations

import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List
from threading import Semaphore
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, TimeoutException

from utils.csvlog import CsvLogger
from utils.classify import classify_solver

POW_COOKIE_NAME = "pow-shield"
BLOCK_KEYWORDS = (
    "proof-of-work", "proof of work",
    "calculating nonce", "submitting result",
    "solve the challenge",
)

# Low-latency but safe timings
POLL_SLEEP_S = 0.18
POST_COOKIE_REPLACE_WAIT_S = 1.2
STABILIZE_SLEEP_S = 0.35
NUDGES_MAX = 2
NUDGE_SLEEP_S = 0.45


# ----------------------------
# WebDriver helpers
# ----------------------------
def _make_driver(chrome_binary: Optional[str] = None, headless: bool = True) -> webdriver.Chrome:
    """Create a Chrome WebDriver using Selenium Manager."""
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,800")
    # Faster navigations: stop at DOMContentLoaded
    try:
        opts.page_load_strategy = "eager"
    except Exception:
        pass
    if chrome_binary:
        opts.binary_location = chrome_binary
    return webdriver.Chrome(options=opts)


def _has_pow_cookie(driver) -> bool:
    """Return True if the PoW cookie is present (any scope)."""
    try:
        c = driver.get_cookie(POW_COOKIE_NAME)
        if c and c.get("value"):
            return True
    except Exception:
        pass
    try:
        for c in driver.get_cookies() or []:
            if c.get("name") == POW_COOKIE_NAME and c.get("value"):
                return True
    except Exception:
        pass
    try:
        dc = driver.execute_script("return document.cookie") or ""
        if f"{POW_COOKIE_NAME}=" in dc:
            return True
    except Exception:
        pass
    return False


def _ensure_root_cookie(drv, target: str) -> bool:
    """Duplicate pow-shield to Path=/; force secure=False on HTTP; prefer host-only cookie."""
    try:
        ck = drv.get_cookie(POW_COOKIE_NAME)
        if not ck or not ck.get("value"):
            return False

        parsed = urlparse(target)
        is_https = (parsed.scheme.lower() == "https")
        host = parsed.hostname or ck.get("domain", "")

        new_cookie = {
            "name": POW_COOKIE_NAME,
            "value": ck["value"],
            "path": "/",
        }
        if "sameSite" in ck:
            new_cookie["sameSite"] = ck["sameSite"]
        if "expiry" in ck:
            new_cookie["expiry"] = ck["expiry"]

        # On HTTP, cookie must not be Secure
        new_cookie["secure"] = ck.get("secure", True) if is_https else False

        # 1) Host-only
        try:
            drv.add_cookie(new_cookie)
            return True
        except Exception:
            pass

        # 2) Fallback with explicit domain
        try:
            new_cookie["domain"] = host
            drv.add_cookie(new_cookie)
            return True
        except Exception:
            return False
    except Exception:
        return False


def _is_blocked_text(html_text: str) -> bool:
    """Heuristically detect a challenge/blocked page by keywords."""
    t = (html_text or "").lower()
    return any(k in t for k in BLOCK_KEYWORDS)


def _nudge_to_root(drv, target: str) -> List[str]:
    """Minimal nudges to reach root. Returns observed URLs (annotated hops)."""
    hops: List[str] = []
    # Strong JS navigation
    try:
        drv.execute_script("window.location.replace('/')")
        time.sleep(NUDGE_SLEEP_S)
        cur = drv.current_url or ""
        if cur:
            hops.append(f"[js] {cur}")
    except Exception:
        pass

    # Direct GET as fallback
    try:
        drv.get(target)
        time.sleep(NUDGE_SLEEP_S)
        cur = drv.current_url or ""
        if cur:
            hops.append(f"[get] {cur}")
    except Exception:
        pass

    return [u for u in hops if u]


# ----------------------------
# Single attempt
# ----------------------------
def _attempt_once(
    target: str,
    wait_s: float,
    chrome_binary: Optional[str],
    debug: bool,
    assume_no_pow: bool = False,
) -> Tuple[bool, int, str, str, str, str, str, str, str]:
    """Perform one browser attempt.

    Returns:
        (solved, latency_ms, reason, requested_url, redirect_url, landed_url, url_chain, last_page_text, error_text)
    """
    t0 = time.perf_counter()
    drv = None
    solved = False
    reason = "timeout"
    error_text = ""

    requested_url = target
    redirect_url = ""   # where we first land after initial navigation (often /pow)
    landed_url = ""     # final observed URL after solving/nudges
    last_src = ""
    chain: list[str] = []

    try:
        drv = _make_driver(chrome_binary=chrome_binary, headless=True)
        drv.set_page_load_timeout(max(40.0, wait_s + 5.0))
        drv.set_script_timeout(max(40.0, wait_s + 5.0))

        # Always include the requested URL in the chain (even if redirect is instant)
        chain.append(requested_url)

        # Initial navigation
        drv.get(requested_url)
        try:
            redirect_url = drv.current_url or ""
            last_src = drv.page_source or ""
        except Exception:
            redirect_url = ""
            last_src = ""

        if redirect_url and (not chain or redirect_url != chain[-1]):
            chain.append(redirect_url)
        if debug:
            print(f"[solver][dbg] requested={requested_url} redirect={redirect_url}")

        # -------------------------
        # NEW: Immediate no-PoW fast path
        # If we are NOT on /pow and page doesn't look like a challenge, treat as success.
        # If --assume-no-pow is set, be even more aggressive.
        cur_is_pow = ("/pow" in (redirect_url or "").lower())
        looks_blocked = _is_blocked_text(last_src)
        if assume_no_pow:
            if not looks_blocked and not cur_is_pow:
                solved = True
                reason = "no_pow_assumed"
                landed_url = redirect_url or requested_url
                raise StopIteration  # jump to post-handling
        else:
            if not looks_blocked and not cur_is_pow:
                solved = True
                reason = "no_pow_detected"
                landed_url = redirect_url or requested_url
                raise StopIteration
        # -------------------------

        deadline = time.time() + wait_s
        last_seen_url = redirect_url or requested_url

        while time.time() < deadline:
            try:
                cur_url = drv.current_url or ""
                src = drv.page_source or ""
                last_src = src
            except Exception:
                cur_url, src = "", ""

            if cur_url and cur_url != last_seen_url:
                chain.append(cur_url)
                last_seen_url = cur_url
                if debug:
                    print(f"[dbg][hop] {last_seen_url}")

            # If we ever find ourselves out of /pow and not looking blocked, we're done.
            if cur_url and "/pow" not in cur_url.lower() and not _is_blocked_text(src):
                solved = True
                reason = "left_pow_or_normal_page"
                landed_url = cur_url
                break

            # Fast path: cookie present -> normalize cookie -> jump to target
            if _has_pow_cookie(drv):
                try:
                    _ensure_root_cookie(drv, requested_url)
                except Exception:
                    pass

                # Attempt 1: GET to requested target
                try:
                    drv.get(requested_url)
                except Exception:
                    pass

                end_wait = time.time() + 3.0
                off_pow = False
                while time.time() < end_wait:
                    try:
                        cur2 = drv.current_url or ""
                        src2 = drv.page_source or ""
                        last_src = src2 or last_src
                    except Exception:
                        cur2 = ""

                    if cur2 and cur2 != last_seen_url:
                        chain.append(cur2)
                        last_seen_url = cur2

                    if cur2 and "/pow" not in cur2.lower() and not _is_blocked_text(last_src):
                        off_pow = True
                        landed_url = cur2
                        break
                    time.sleep(0.15)

                # Attempt 2: JS replace to the requested target
                if not off_pow:
                    try:
                        drv.execute_script("window.location.replace(arguments[0])", requested_url)
                    except Exception:
                        pass

                    end_wait2 = time.time() + 2.0
                    while time.time() < end_wait2:
                        try:
                            cur3 = drv.current_url or ""
                            src3 = drv.page_source or ""
                            last_src = src3 or last_src
                        except Exception:
                            cur3 = ""

                        if cur3 and cur3 != last_seen_url:
                            chain.append(cur3)
                            last_seen_url = cur3

                        if cur3 and "/pow" not in cur3.lower() and not _is_blocked_text(last_src):
                            off_pow = True
                            landed_url = cur3
                            break
                        time.sleep(0.15)

                if off_pow:
                    solved = True
                    reason = "cookie_then_target"
                else:
                    solved = False
                    reason = "cookie_but_stuck"
                    landed_url = (drv.current_url or last_seen_url or redirect_url or requested_url)

                break  # exit polling loop

        # Post-solve nudges if still on /pow
        if solved:
            if "/pow" in (landed_url or last_seen_url or "").lower():
                for _ in range(NUDGES_MAX):
                    hops = _nudge_to_root(drv, requested_url)
                    for h in hops:
                        url_only = h.split(" ", 1)[-1]
                        if not chain or url_only != chain[-1]:
                            chain.append(h)  # keep annotation
                            last_seen_url = url_only
                    try:
                        cur_after = drv.current_url or ""
                        last_src = drv.page_source or last_src
                    except Exception:
                        cur_after = ""
                    if cur_after and "/pow" not in cur_after.lower():
                        landed_url = cur_after
                        break
                    time.sleep(NUDGE_SLEEP_S)

            if not landed_url:
                try:
                    landed_url = drv.current_url or last_seen_url or redirect_url or requested_url
                except Exception:
                    landed_url = last_seen_url or redirect_url or requested_url

        if not solved:
            try:
                src = drv.page_source or ""
                last_src = src or last_src
                if _is_blocked_text(src):
                    reason = "challenge_page"
            except Exception:
                pass

        if not landed_url:
            landed_url = last_seen_url or redirect_url or requested_url

    except StopIteration:
        # Used for intentional early-exit fast paths; 'solved'/'reason' already set
        try:
            landed_url = landed_url or redirect_url or requested_url
        except Exception:
            landed_url = redirect_url or requested_url
    except (TimeoutException, WebDriverException) as e:
        reason = type(e).__name__
        error_text = f"{type(e).__name__}: {e}"
    except Exception as e:
        reason = type(e).__name__
        error_text = f"{type(e).__name__}: {e}"
    finally:
        try:
            if drv:
                drv.quit()
        except Exception:
            pass

    t1 = time.perf_counter()
    latency_ms = int((t1 - t0) * 1000)
    chain_str = " -> ".join(chain) if chain else requested_url
    return solved, latency_ms, reason, requested_url, redirect_url, landed_url, chain_str, last_src, error_text


# ----------------------------
# Runner
# ----------------------------
def _pct(n: int, d: int) -> float:
    """Return percentage n/d rounded to two decimals (0.0 if d==0)."""
    return round((100.0 * n / d), 2) if d > 0 else 0.0


def run(
    target: str,
    browsers: int,
    iterations: int,
    wait: float,
    max_concurrent: int,
    results_dir: Path,
    with_detail: bool = False,
    *,
    chrome_binary: Optional[str] = None,
    debug: bool = False,
    assume_no_pow: bool = False,  # NEW: aggressive fast-path for known PoW-OFF runs
):
    """Execute the Selenium-based solver test and write CSV outputs."""
    test_name = "solver"
    logger = CsvLogger(results_dir=results_dir, test_name=test_name, with_detail=with_detail)

    sem = Semaphore(max_concurrent)
    lock = threading.Lock()

    requests_sent = 0
    passed = 0
    blocked = 0
    errors = 0
    lat_sum = 0
    lat_cnt = 0

    def worker(bid: int):
        nonlocal requests_sent, passed, blocked, errors, lat_sum, lat_cnt
        for it in range(iterations):
            try:
                with sem:
                    (solved, latency_ms, reason,
                     requested_url, redirect_url, landed_url, url_chain, last_src, error_text) = _attempt_once(
                        target=target,
                        wait_s=wait,
                        chrome_binary=chrome_binary,
                        debug=debug,
                        assume_no_pow=assume_no_pow,
                    )

                is_exception = reason.endswith("Exception") or reason in {
                    "TimeoutException", "WebDriverException"
                }

                cls = classify_solver(
                    status_code=None,
                    url=landed_url or "",
                    is_redirect=(requested_url != redirect_url and bool(redirect_url)),
                    location="",
                    page_text=last_src or "",
                    passed_pow=bool(solved and (landed_url and "/pow" not in landed_url.lower())),
                    had_exception=is_exception,
                )

                # Safety net: never "ok" if still on /pow
                if cls == "ok" and (not landed_url or "/pow" in landed_url.lower()):
                    cls = "blocked"

                # If we short-circuited as solved and we're not on /pow and page isn't challenge-like, force ok
                if cls != "ok" and solved:
                    if (landed_url and "/pow" not in landed_url.lower()) and not _is_blocked_text(last_src):
                        cls = "ok"

                with lock:
                    requests_sent += 1
                    lat_sum += latency_ms
                    lat_cnt += 1
                    if cls == "ok":
                        passed += 1
                    elif cls == "error":
                        errors += 1
                        blocked += 1
                    else:
                        blocked += 1

                if with_detail:
                    logger.write_detail({
                        "ts_iso": datetime.utcnow().isoformat(),
                        "test": test_name,
                        "worker_id": bid,
                        "iteration": it,
                        "status_code": 200 if cls == "ok" else "",
                        "class": cls,
                        "latency_ms": latency_ms,
                        "is_redirect": (requested_url != redirect_url and bool(redirect_url)),
                        "requested_url": requested_url,
                        "redirect_url": redirect_url,
                        "landed_url": landed_url,
                        "url_chain": url_chain,
                        "location": "",
                        "reason": reason,
                        "error": error_text,
                    })

                time.sleep(0.1)
            except Exception as e:
                with lock:
                    blocked += 1
                    errors += 1
                    requests_sent += 1
                if with_detail:
                    logger.write_detail({
                        "ts_iso": datetime.utcnow().isoformat(),
                        "test": test_name,
                        "worker_id": bid,
                        "iteration": it,
                        "status_code": "",
                        "class": "error",
                        "latency_ms": 0,
                        "is_redirect": "",
                        "requested_url": target,
                        "redirect_url": "",
                        "landed_url": "",
                        "url_chain": target,
                        "location": "",
                        "reason": type(e).__name__,
                        "error": f"{type(e).__name__}: {e}",
                    })

    print(f"[solver] target={target} browsers={browsers} iter={iterations} wait={wait}s max_concurrent={max_concurrent} assume_no_pow={assume_no_pow}")

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(browsers)]
    for t in threads:
        t.start()
        time.sleep(0.02)
    for t in threads:
        t.join()

    avg_latency = round((lat_sum / lat_cnt), 2) if lat_cnt else 0.0

    summary_row = {
        "timestamp": datetime.utcnow().isoformat(),
        "which": test_name,
        "requests_sent": requests_sent,
        "passed": passed,
        "blocked": blocked,
        "errors": errors,
        "avg_latency_ms": avg_latency,
        "pct_passed": _pct(passed, requests_sent),
        "pct_blocked": _pct(blocked, requests_sent),
        "pct_errors": _pct(errors, requests_sent),
        "note": f"browsers={browsers} iter={iterations} wait={wait}s max_concurrent={max_concurrent} assume_no_pow={assume_no_pow}",
    }
    logger.write_summary(summary_row)
    logger.close()

    print(
        f"[solver][summary] sent={requests_sent} ok={passed} blocked={blocked} errors={errors} "
        f"avg_latency_ms={avg_latency} "
        f"({summary_row['pct_passed']}% ok, {summary_row['pct_blocked']}% blocked, {summary_row['pct_errors']}% errors)"
    )
