"""
channel.py
Faster + demo-friendly LEO channel simulation
- shorter delays
- link UP most of the time
- brief outages instead of long ones
"""

import time, random, threading

# These values make the simulated satellite link feel realistic but still quick.
BASE_DELAY_MS = 120        # normal latency
JITTER_MS     = 80         # random variation
LOSS_PROB     = 0.03       # 3% theoretical packet loss
UP_TIME_S     = 45         # link stays up for 45 sec
DOWN_TIME_S   = 8          # link down only 8 sec

_link_up = True
# Shared channel statistics used by the satellite status screen.
_stats = {
    "delay_samples": [],
    "packets_total": 0,
    "packets_lost": 0,
    "avg_delay_ms": BASE_DELAY_MS
}
_lock = threading.Lock()

def channel_delay():
    """Sleep for a short simulated satellite delay."""
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
    """Randomly decide whether a packet is lost."""
    lost = random.random() < LOSS_PROB
    with _lock:
        _stats["packets_total"] += 1
        if lost:
            _stats["packets_lost"] += 1
    return lost

def is_link_up():
    """Return whether the satellite link is currently visible."""
    return _link_up

def visibility_manager():
    """Switch the simulated link between up and down windows."""
    global _link_up
    while True:
        _link_up = True
        time.sleep(UP_TIME_S)
        _link_up = False
        time.sleep(DOWN_TIME_S)

def get_stats():
    """Return the latest delay and packet-loss statistics."""
    with _lock:
        total = _stats["packets_total"]
        lost = _stats["packets_lost"]
        actual_loss = round((lost / total) * 100, 2) if total > 0 else 0.0

        return {
            "loss_pct": actual_loss,
            "avg_delay_ms": _stats["avg_delay_ms"],
            "link_up": _link_up,
            "up_time_s": UP_TIME_S,
            "down_time_s": DOWN_TIME_S,
            "packets_total": total,
            "packets_lost": lost
        }
