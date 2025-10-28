"""Classification helpers for load tests.

Provides deterministic classifiers for three scenarios:
- burst: plain HTTP responses without JS (no redirect follow)
- simple: requests-based clients that follow redirects (no JS)
- solver: Selenium-based clients that can execute JS and pass PoW

Each classifier returns one of: "ok", "blocked", "error", or "other".
"""

from __future__ import annotations
from dataclasses import dataclass

# Keywords that indicate a PoW/challenge page in the body
KEYWORDS = (
    "proof-of-work", "proof of work",
    "calculating nonce", "submitting result",
    "solve the challenge",
)

@dataclass
class ClassifyConfig:
    """Config kept for API parity and flexibility across classifiers."""
    mode: str = "status_only"
    pow_path: str = "/pow"  # path used by the PoW challenge (configurable)

# ---------------------------
# BURST (no redirect follow)
# ---------------------------
def classify_burst(response, cfg: ClassifyConfig) -> str:
    """Classify a plain HTTP response for the 'burst' test.

    Correct rules for PoW:
      - 3xx with Location pointing to /pow => "blocked"
      - 401/403/429 => "blocked"
      - 2xx => "ok"
      - other => "error"/"other" (we return 'other' for non-/pow redirects)
    """
    sc = getattr(response, "status_code", None)
    if sc is None:
        return "error"

    # Redirect → /pow ⇒ blocked
    if 300 <= sc < 400:
        loc = (getattr(response, "headers", {}) or {}).get("Location", "")
        if loc and cfg.pow_path.lower() in loc.lower():
            return "blocked"
        return "other"  # other redirects: keep visible as 'other'

    if sc in (401, 403, 429):
        return "blocked"

    if 200 <= sc < 300:
        return "ok"

    return "error"

# ---------------------------
# SIMPLE (redirects followed)
# ---------------------------
def classify_simple(response, cfg: ClassifyConfig) -> str:
    """Classify a requests-based response for the 'simple' test.

    Follows HTTP redirects but does not execute JS. Any evidence of the PoW
    route (/pow) or a challenge-like body is considered "blocked".
    """
    sc = getattr(response, "status_code", None)
    url = (getattr(response, "url", "") or "").lower()

    # If a redirect was present, inspect its Location
    if getattr(response, "is_redirect", False):
        loc = (response.headers.get("Location", "") or "").lower()
        if cfg.pow_path.lower() in loc:
            return "blocked"
        return "other"

    # Landed on /pow? Still blocked.
    if cfg.pow_path.lower() in url:
        return "blocked"

    # Explicit blocks
    if sc in (401, 403, 429):
        return "blocked"

    # 2xx can still be a challenge page (body-based detection)
    if sc is not None and 200 <= sc < 300:
        t = (getattr(response, "text", "") or "").lower()
        if any(k in t for k in KEYWORDS):
            return "blocked"
        return "ok"

    return "other"

# ---------------------------
# SOLVER (JS-capable)
# ---------------------------
def classify_solver(
    *,
    status_code: int | None,
    url: str,
    is_redirect: bool = False,
    location: str = "",
    page_text: str = "",
    passed_pow: bool = False,
    had_exception: bool = False,
) -> str:
    """Classify the end-state for a Selenium-based solver attempt.

    Mirrors 'classify_simple' but supports JS-driven transitions. A positive
    shortcut is taken when `passed_pow` is True and the URL is not on /pow.
    """
    if had_exception:
        return "error"

    cur_url = (url or "").lower()

    # If the solver explicitly signaled success and we're not on /pow: ok
    if passed_pow and "/pow" not in cur_url:
        return "ok"

    if is_redirect and "/pow" in (location or "").lower():
        return "blocked"

    if "/pow" in cur_url:
        return "blocked"

    t = (page_text or "").lower()
    if any(k in t for k in KEYWORDS):
        return "blocked"

    sc = status_code
    if sc in (401, 403, 429):
        return "blocked"
    if sc is not None and 200 <= sc < 300:
        return "ok"

    return "other"
