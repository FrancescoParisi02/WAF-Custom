from __future__ import annotations
from dataclasses import dataclass

KEYWORDS = (
    "proof-of-work", "proof of work", "calculating nonce", "submitting result"
)

@dataclass
class ClassifyConfig:
    mode: str = "status_only"  # 'status_only' for burst, 'waf' per simple

# ---- Burst: conservativo e semplice ----
def classify_burst(response, cfg: ClassifyConfig) -> str:
    sc = getattr(response, "status_code", None)
    if sc is None:
        return "other"
    if 200 <= sc < 300:
        return "ok"
    if sc in (401, 403, 429):
        return "blocked"
    if 300 <= sc < 400:
        return "other"
    return "other"

# ---- Simple: segue redirect, niente 'challenge' generico ----
def classify_simple(response, cfg: ClassifyConfig) -> str:
    sc = getattr(response, "status_code", None)
    url = getattr(response, "url", "") or ""
    if getattr(response, "is_redirect", False):
        loc = response.headers.get("Location", "")
        if "/pow" in loc:
            return "blocked"
        return "other"
    if "/pow" in url:
        return "blocked"
    if sc in (401, 403, 429):
        return "blocked"
    if 200 <= sc < 300:
        t = (response.text or "").lower()
        if any(k in t for k in KEYWORDS):
            return "blocked"
        return "ok"
    return "other"
