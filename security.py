"""
CSU33D03 - Main Project 2025-26
Group 9 - Security Layer

HMAC-SHA256 message signing and replay attack prevention.
Sits in the project root, imported by all three nodes.

    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from security import sign_message, verify_message, strip_security_fields
"""

import hmac, hashlib, json, time

HMAC_SECRET   = b"csu33d03-group9-arklow-2026"
HMAC_FIELD    = "sig"
REPLAY_WINDOW = 30   # seconds - reject messages older than this


def sign_message(msg: dict) -> dict:
    """Add HMAC-SHA256 signature + timestamp. Call before every send."""
    msg.pop(HMAC_FIELD, None)
    msg["sent_at"] = time.time()
    payload = json.dumps(msg, sort_keys=True, separators=(",", ":"))
    msg[HMAC_FIELD] = hmac.new(HMAC_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return msg


def verify_message(msg: dict) -> tuple:
    """
    Verify signature and freshness of an incoming message.
    Returns (True, "") or (False, reason_string).
    """
    received_sig = msg.get(HMAC_FIELD)
    if not received_sig:
        return False, "missing signature"

    sent_at = msg.get("sent_at")
    if sent_at is None:
        return False, "missing timestamp"

    age = time.time() - float(sent_at)
    if age > REPLAY_WINDOW:
        return False, f"replay rejected (age={age:.1f}s)"
    if age < -5:
        return False, f"clock skew too large"

    check = dict(msg)
    check.pop(HMAC_FIELD)
    payload  = json.dumps(check, sort_keys=True, separators=(",", ":"))
    expected = hmac.new(HMAC_SECRET, payload.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(received_sig, expected):
        return False, "signature mismatch"
    return True, ""


def strip_security_fields(msg: dict) -> dict:
    """Remove sig and sent_at before passing to application logic."""
    clean = dict(msg)
    clean.pop(HMAC_FIELD, None)
    clean.pop("sent_at", None)
    return clean
