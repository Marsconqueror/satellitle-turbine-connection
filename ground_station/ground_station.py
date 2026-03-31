"""
CSU33D03 - Main Project 2025-26
GROUND CONTROL STATION  -  Device C
Cleaned version:
- telemetry spam removed
- easier terminal use
"""

import socket, threading, time, json, logging, sys, os
from datetime import datetime
from collections import defaultdict, deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from security import sign_message, verify_message, strip_security_fields

# =============================================================================
# Config
# =============================================================================
SATELLITE_HOST  = "127.0.0.1"
SATELLITE_PORT  = 9001
GROUND_ID       = "GROUND-CTRL-01"
RECONNECT_DELAY = 5
HISTORY_LEN     = 200

ALERT_THRESHOLDS = {
    "wind_speed":          {"min": 0,    "max": 25,   "unit": "m/s"},
    "power_output":        {"min": 0,    "max": 2000, "unit": "kW"},
    "rotor_rpm":           {"min": 0,    "max": 20,   "unit": "RPM"},
    "temperature":         {"min": -10,  "max": 70,   "unit": "C"},
    "gearbox_temp":        {"min": -10,  "max": 90,   "unit": "C"},
    "vibration":           {"min": 0,    "max": 8.0,  "unit": "mm/s"},
    "hydraulic_pressure":  {"min": 100,  "max": 300,  "unit": "bar"},
    "nacelle_humidity":    {"min": 0,    "max": 85,   "unit": "%"},
}

AUTO_ESTOP_THRESHOLDS = {
    "temperature":   65,
    "gearbox_temp":  88,
    "vibration":     7.5,
    "wind_speed":    25,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GROUND] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("ground")

telemetry_history = defaultdict(lambda: deque(maxlen=HISTORY_LEN))
known_turbines    = {}
state_lock        = threading.Lock()

_sat_sock        = None
_sock_lock       = threading.Lock()
connected_to_sat = False

_cmd_counter = 0
_cmd_lock    = threading.Lock()
_rtt_samples = deque(maxlen=20)
_seq_expected = {}

def _next_id():
    global _cmd_counter
    with _cmd_lock:
        _cmd_counter += 1
        return f"CMD-{_cmd_counter:05d}"

def send_to_sat(msg: dict) -> bool:
    signed = sign_message(msg)
    with _sock_lock:
        if _sat_sock is None:
            log.error("Not connected to satellite")
            return False
        try:
            _sat_sock.sendall((json.dumps(signed) + "\n").encode())
            return True
        except OSError as e:
            log.error(f"Send failed: {e}")
            return False

def send_command(turbine_id, action, params=None):
    cid = _next_id()
    send_to_sat({
        "type":       "COMMAND",
        "turbine_id": turbine_id,
        "ground_id":  GROUND_ID,
        "cmd_id":     cid,
        "action":     action,
        "params":     params or {},
        "timestamp":  datetime.utcnow().isoformat() + "Z",
    })
    log.info(f"Sent {action} -> {turbine_id} ({cid})")

def connect_loop():
    global _sat_sock, connected_to_sat
    while True:
        log.info(f"Connecting to satellite {SATELLITE_HOST}:{SATELLITE_PORT} ...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((SATELLITE_HOST, SATELLITE_PORT))
            sock.settimeout(5)
            with _sock_lock:
                _sat_sock = sock
            send_to_sat({
                "type":      "REGISTER",
                "node_type": "GROUND",
                "ground_id": GROUND_ID,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })
            connected_to_sat = True
            log.info("Connected to satellite")
            _receive_loop(sock)
        except (ConnectionRefusedError, OSError) as e:
            log.warning(f"Cannot reach satellite: {e} - retry in {RECONNECT_DELAY}s")
        except Exception as e:
            log.error(f"Connection error: {e} - retry in {RECONNECT_DELAY}s")
        finally:
            connected_to_sat = False
            with _sock_lock:
                _sat_sock = None
        time.sleep(RECONNECT_DELAY)

def _receive_loop(sock):
    buffer = ""
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                log.warning("Satellite closed connection")
                break
            buffer += chunk.decode()
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ok, reason = verify_message(msg)
                if not ok:
                    log.warning(f"Rejected message: {reason}")
                    continue
                _dispatch(strip_security_fields(msg))
        except socket.timeout:
            continue
        except OSError:
            break

def _dispatch(msg):
    t = msg.get("type", "")

    if t == "REGISTER_ACK":
        log.info(f"Registered with satellite {msg.get('satellite_id')}")

    elif t == "TELEMETRY":
        _process_telemetry(msg)

    elif t == "ACK":
        status = "OK" if msg.get("success") else "FAIL"
        log.info(f"ACK [{status}] [{msg.get('turbine_id')}] "
                 f"'{msg.get('action')}': {msg.get('message')}")

    elif t == "ROUTE_ACK":
        if msg.get("queued"):
            log.warning(f"Command '{msg.get('action')}' QUEUED (link down)")
        else:
            log.info(f"Command '{msg.get('action')}' routed to {msg.get('turbine_id')}")

    elif t == "TURBINE_BEACON":
        tid = msg.get("turbine_id")
        log.info(f"Beacon from {tid}")
        with state_lock:
            if tid not in known_turbines:
                known_turbines[tid] = {}

    elif t == "DISCOVER_RESPONSE":
        turbines = msg.get("turbines", [])
        log.info(f"Discovery: {len(turbines)} turbine(s) | "
                 f"link={'UP' if msg.get('link_up') else 'DOWN'}")
        with state_lock:
            for ti in turbines:
                known_turbines[ti["turbine_id"]] = ti.get("meta", {})

    elif t == "PONG":
        sent_at = msg.get("sent_at_echo")
        if sent_at:
            rtt = (time.time() - float(sent_at)) * 1000
            _rtt_samples.append(rtt)
            avg = sum(_rtt_samples) / len(_rtt_samples)
            log.info(f"PONG - link={'UP' if msg.get('link_up') else 'DOWN'} "
                     f"RTT={rtt:.1f}ms avg={avg:.1f}ms")
        else:
            log.info(f"PONG - link={'UP' if msg.get('link_up') else 'DOWN'}")

    elif t == "SATELLITE_STATUS":
        cs = msg.get("channel_stats", {})
        log.info(f"SAT STATUS | link={'UP' if msg.get('link_up') else 'DOWN'} | "
                 f"turbines={msg.get('turbines_online')} | "
                 f"loss={cs.get('loss_pct')}% | "
                 f"avg_delay={cs.get('avg_delay_ms')}ms")

def _process_telemetry(msg):
    tid     = msg.get("turbine_id", "?")
    sensors = msg.get("sensors", {})
    seq     = msg.get("seq")

    if seq is not None:
        expected = _seq_expected.get(tid)
        if expected is not None and seq != expected:
            dropped = seq - expected
            if dropped > 0:
                log.warning(f"Seq gap on {tid}: {dropped} packet(s) lost")
        _seq_expected[tid] = seq + 1

    msg["received_at"] = time.time()
    with state_lock:
        known_turbines[tid] = msg
        telemetry_history[tid].append(msg)

    # No spam live printing here anymore

    for sensor, th in ALERT_THRESHOLDS.items():
        val = sensors.get(sensor)
        if val is not None and (val < th["min"] or val > th["max"]):
            log.warning(f"ALERT [{tid}] {sensor}={val}{th['unit']} "
                        f"(range {th['min']}-{th['max']})")

    for sensor, limit in AUTO_ESTOP_THRESHOLDS.items():
        val = sensors.get(sensor)
        if val is not None and val > limit:
            log.warning(f"AUTO ESTOP [{tid}] {sensor}={val} exceeded {limit}")
            send_command(tid, "EMERGENCY_STOP")
            break

def display_status():
    print("\n" + "=" * 68)
    print(f"  GROUND CONTROL  -  {datetime.utcnow().strftime('%H:%M:%S')} UTC")
    print(f"  Satellite : {'CONNECTED' if connected_to_sat else 'DISCONNECTED'}")
    if _rtt_samples:
        avg = sum(_rtt_samples) / len(_rtt_samples)
        print(f"  Avg RTT   : {avg:.1f}ms")
    print("=" * 68)
    with state_lock:
        if not known_turbines:
            print("  No turbines yet - run: discover")
        for tid, data in known_turbines.items():
            s  = data.get("sensors", {})
            d  = data.get("derived", {})
            a  = data.get("actuators", {})
            st = data.get("status", {})
            m  = data.get("meta", {})
            estop = "E-STOP" if st.get("emergency_stop") else (
                    "MAINT"  if st.get("maintenance_mode") else "Normal")
            leader = "LEADER" if m.get("is_leader") else "standby"
            print(f"\n  Turbine : {tid}  [{estop}]  [{leader}]")
            print(f"  --- Sensors ---")
            print(f"    Wind speed        : {s.get('wind_speed','?'):>8} m/s")
            print(f"    Power output      : {s.get('power_output','?'):>8} kW")
            print(f"    Rotor RPM         : {s.get('rotor_rpm','?'):>8} RPM")
            print(f"    Temperature       : {s.get('temperature','?'):>8} C")
            print(f"    Gearbox temp      : {s.get('gearbox_temp','?'):>8} C")
            print(f"    Vibration         : {s.get('vibration','?'):>8} mm/s")
            print(f"    Hydraulic press.  : {s.get('hydraulic_pressure','?'):>8} bar")
            print(f"    Nacelle humidity  : {s.get('nacelle_humidity','?'):>8} %")
            print(f"  --- Derived ---")
            print(f"    Capacity factor   : {d.get('capacity_factor','?')}")
            print(f"    Tip speed ratio   : {d.get('tip_speed_ratio','?')}")
            print(f"    Power coefficient : {d.get('power_coefficient','?')}")
            print(f"    Mech efficiency   : {d.get('mech_efficiency','?')}")
            print(f"    AEP projection    : {d.get('aep_projection_mwh','?')} MWh")
            print(f"  --- Actuators ---")
            print(f"    Yaw angle         : {a.get('yaw_angle','?'):>8} deg")
            print(f"    Blade pitch       : {a.get('blade_pitch','?'):>8} deg")
    print("=" * 68 + "\n")

def display_history(tid, n=10):
    with state_lock:
        hist = list(telemetry_history.get(tid, []))[-n:]
    if not hist:
        print(f"No history for {tid}")
        return
    print(f"\nLast {len(hist)} readings for {tid}:")
    print(f"  {'Timestamp':<25} {'Wind':>6} {'Power':>7} {'Temp':>6} {'Vib':>6} {'Seq':>5}")
    print("  " + "-" * 58)
    for e in hist:
        s = e.get("sensors", {})
        print(f"  {e.get('timestamp','?'):<25} "
              f"{s.get('wind_speed','?'):>6} "
              f"{s.get('power_output','?'):>7} "
              f"{s.get('temperature','?'):>6} "
              f"{s.get('vibration','?'):>6} "
              f"{e.get('seq','?'):>5}")

def display_trend(tid):
    with state_lock:
        hist = list(telemetry_history.get(tid, []))[-20:]
    if len(hist) < 2:
        print("Not enough data - need at least 2 readings")
        return
    print(f"\nTrend analysis for {tid} (last {len(hist)} readings):")
    for k in ["wind_speed", "power_output", "temperature", "vibration", "gearbox_temp"]:
        vals = [h.get("sensors", {}).get(k) for h in hist
                if h.get("sensors", {}).get(k) is not None]
        if not vals:
            continue
        avg   = sum(vals) / len(vals)
        trend = "UP" if vals[-1] > vals[0] else ("DOWN" if vals[-1] < vals[0] else "FLAT")
        print(f"  {k:<22}: avg={avg:.1f}  min={min(vals):.1f}  "
              f"max={max(vals):.1f}  trend={trend}")

HELP = """
  status                           full turbine dashboard
  discover                         find turbines via satellite
  ping                             ping satellite (measures RTT)
  sat                              satellite health + channel stats
  turbines                         list known turbine IDs

  yaw    <id> <0-360>              set yaw angle
  pitch  <id> <0-90>               set blade pitch
  estop  <id>                      emergency stop
  resume <id>                      resume after estop
  maint  <id> on|off               toggle maintenance mode

  history  <id> [n]                last N readings (default 10)
  trend    <id>                    trend analysis of last 20 readings
  quit                             exit
"""

def cli():
    time.sleep(2)
    print("\nGround Control Station ready. Type 'help'.\n")
    while True:
        try:
            raw = input("ground> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            sys.exit(0)
        if not raw:
            continue
        parts = raw.split()
        cmd   = parts[0].lower()

        if cmd == "help":
            print(HELP)
        elif cmd == "status":
            display_status()
        elif cmd == "discover":
            send_to_sat({"type": "DISCOVER", "ground_id": GROUND_ID,
                         "timestamp": datetime.utcnow().isoformat() + "Z"})
        elif cmd == "ping":
            send_to_sat({"type": "PING", "ground_id": GROUND_ID,
                         "sent_at_echo": time.time(),
                         "timestamp": datetime.utcnow().isoformat() + "Z"})
        elif cmd == "sat":
            send_to_sat({"type": "STATUS_REQUEST", "ground_id": GROUND_ID,
                         "timestamp": datetime.utcnow().isoformat() + "Z"})
        elif cmd == "turbines":
            with state_lock:
                ids = list(known_turbines.keys())
            print("Known:", ids if ids else "(none - run discover)")
        elif cmd == "yaw" and len(parts) >= 3:
            try:
                send_command(parts[1], "SET_YAW", {"angle": float(parts[2])})
            except ValueError:
                print("Angle must be a number")
        elif cmd == "pitch" and len(parts) >= 3:
            try:
                send_command(parts[1], "SET_PITCH", {"pitch": float(parts[2])})
            except ValueError:
                print("Angle must be a number")
        elif cmd == "estop" and len(parts) >= 2:
            send_command(parts[1], "EMERGENCY_STOP")
        elif cmd == "resume" and len(parts) >= 2:
            send_command(parts[1], "RESUME")
        elif cmd == "maint" and len(parts) >= 3:
            send_command(parts[1], "SET_MAINTENANCE",
                         {"active": parts[2].lower() == "on"})
        elif cmd == "history" and len(parts) >= 2:
            n = int(parts[2]) if len(parts) >= 3 else 10
            display_history(parts[1], n)
        elif cmd == "trend" and len(parts) >= 2:
            display_trend(parts[1])
        elif cmd in ("quit", "exit", "q"):
            print("Goodbye.")
            sys.exit(0)
        else:
            print(f"Unknown: '{raw}'. Type 'help'.")

def main():
    log.info("=" * 60)
    log.info(f"  GROUND CONTROL STATION  -  {GROUND_ID}")
    log.info(f"  Satellite : {SATELLITE_HOST}:{SATELLITE_PORT}")
    log.info(f"  Security  : HMAC-SHA256 enabled")
    log.info("=" * 60)
    threading.Thread(target=connect_loop, daemon=True).start()
    cli()

if __name__ == "__main__":
    main()