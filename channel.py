
"""
channel.py
Faster + demo-friendly LEO channel simulation
- shorter delays
- link UP most of the time
- brief outages instead of long ones
"""

import time, random, threading

# ==============================
# DEMO-FRIENDLY SETTINGS
# ==============================
BASE_DELAY_MS = 120        # normal latency
JITTER_MS     = 80         # random variation
LOSS_PROB     = 0.03       # 3% packet loss
UP_TIME_S     = 45         # link stays up for 45 sec
DOWN_TIME_S   = 8          # link down only 8 sec

_link_up = True
_stats = {
    "delay_samples": [],
    "loss_pct": int(LOSS_PROB * 100),
    "avg_delay_ms": BASE_DELAY_MS
}
_lock = threading.Lock()

def channel_delay():
    delay_ms = BASE_DELAY_MS + random.randint(0, JITTER_MS)
    time.sleep(delay_ms / 1000.0)
    with _lock:
        _stats["delay_samples"].append(delay_ms)
        if len(_stats["delay_samples"]) > 100:
            _stats["delay_samples"] = _stats["delay_samples"][-100:]
        _stats["avg_delay_ms"] = round(
            sum(_stats["delay_samples"]) / len(_stats["delay_samples"]), 1
        )

def channel_loss():
    return random.random() < LOSS_PROB

def is_link_up():
    return _link_up

def visibility_manager():
    global _link_up
    while True:
        _link_up = True
        time.sleep(UP_TIME_S)
        _link_up = False
        time.sleep(DOWN_TIME_S)

def get_stats():
    with _lock:
        return {
            "loss_pct": _stats["loss_pct"],
            "avg_delay_ms": _stats["avg_delay_ms"],
            "link_up": _link_up,
            "up_time_s": UP_TIME_S,
            "down_time_s": DOWN_TIME_S
        }