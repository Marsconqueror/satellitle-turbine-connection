"""
CSU33D03 - Main Project 2025-26
GROUND CONTROL STATION  -  Device C

This is the human operator side of the system. It connects to the satellite,
receives live telemetry from wind turbines, displays their status, and lets
the operator send control commands like changing yaw, pitch or triggering an
emergency stop.

Key fixes vs original:
- verify_message() now correctly handled as a tuple (ok, reason).
- Telemetry is flat per-turbine messages - no self/relay_data unpacking needed.
- FARM_ALERT handler: automatically e-stops ALL turbines on high wind reported
  by the leader, then resumes once wind is back in range.
- Auto e-stop on critical temperature still works per-turbine.
- resume logic clears both wind and temperature auto-stop flags.
"""

import socket, threading, time, json, logging, sys, os
from datetime import datetime
from collections import defaultdict, deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from security import sign_message, verify_message, strip_security_fields

SATELLITE_HOST = "172.20.10.3"
SATELLITE_PORT = 9001
GROUND_ID      = "GROUND-CTRL-01"

RECONNECT_DELAY = 5
HISTORY_LEN     = 100

ALERT_THRESHOLDS = {
    "wind_speed":         {"min": 0,    "max": 25,   "unit": "m/s"},
    "power_output":       {"min": 0,    "max": 2000, "unit": "kW"},
    "rotor_rpm":          {"min": 0,    "max": 20,   "unit": "RPM"},
    "temperature":        {"min": -10,  "max": 70,   "unit": "°C"},
    "gearbox_temp":       {"min": -10,  "max": 90,   "unit": "°C"},
    "vibration":          {"min": 0,    "max": 8.0,  "unit": "mm/s"},
    "hydraulic_pressure": {"min": 100,  "max": 300,  "unit": "bar"},
    "nacelle_humidity":   {"min": 0,    "max": 85,   "unit": "%"},
}

WIND_RESUME_THRESHOLD = 20.0   # m/s — farm resumes below this after a wind estop
TEMP_CRITICAL         = 65.0   # °C  — individual turbine auto estop
TEMP_CLEAR            = 60.0   # °C  — temperature back to safe, clear the flag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GROUND] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("ground")

telemetry_history = defaultdict(lambda: deque(maxlen=HISTORY_LEN))
known_turbines    = {}    # tid -> latest telemetry dict
state_lock        = threading.Lock()

_sat_sock        = None
_sock_lock       = threading.Lock()
connected_to_sat = False

# Track auto-stops so we don't spam commands every telemetry packet
auto_estopped_temp = set()    # turbine IDs stopped due to temperature
farm_wind_stopped  = False    # True when the whole farm was stopped for high wind

_cmd_counter = 0
_cmd_lock    = threading.Lock()

def _next_id():
    global _cmd_counter
    with _cmd_lock:
        _cmd_counter += 1
        return f"CMD-{_cmd_counter:05d}"


# ============================================================
# OUTGOING MESSAGES
# ============================================================

def send_to_sat(msg):
    with _sock_lock:
        if _sat_sock is None:
            log.error("Not connected to satellite")
            return False
        try:
            msg = sign_message(msg)
            _sat_sock.sendall((json.dumps(msg) + "\n").encode())
            return True
        except OSError as e:
            log.error(f"Send failed: {e}")
            return False


def send_command(turbine_id, action, params):
    cid = _next_id()
    ok = send_to_sat({
        "type":       "COMMAND",
        "turbine_id": turbine_id,
        "ground_id":  GROUND_ID,
        "cmd_id":     cid,
        "action":     action,
        "params":     params,
        "timestamp":  datetime.utcnow().isoformat() + "Z",
    })
    if ok:
        log.info(f"Sent {action} to {turbine_id} (id={cid})")
    return ok


def send_all(action, params=None):
    """
    Broadcast the same command to ALL turbines via a single ALL-targeted message.
    The satellite will fan it out to every connected turbine in one go, which
    guarantees consistent state (e.g. all blades go to 90° on stopall).
    """
    if params is None:
        params = {}
    if not connected_to_sat:
        print("Not connected to satellite.")
        return
    send_command("ALL", action, params)


def discover():
    send_to_sat({
        "type":      "DISCOVER",
        "ground_id": GROUND_ID,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })


def ping():
    send_to_sat({
        "type":      "PING",
        "ground_id": GROUND_ID,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })


# ============================================================
# CONNECTION / RECEIVE LOOP
# ============================================================

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

            reg = {
                "type":      "REGISTER",
                "node_type": "GROUND",
                "ground_id": GROUND_ID,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            reg = sign_message(reg)
            sock.sendall((json.dumps(reg) + "\n").encode())

            connected_to_sat = True
            log.info("Connected to satellite")
            _receive_loop(sock)

        except (ConnectionRefusedError, OSError) as e:
            log.warning(f"Cannot reach satellite: {e} - retry in {RECONNECT_DELAY}s ...")
        except Exception as e:
            log.error(f"Connection error: {e} - retry in {RECONNECT_DELAY}s ...")
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
                    raw_msg = json.loads(line)

                    # verify_message returns a (bool, reason) tuple
                    ok, reason = verify_message(raw_msg)
                    if not ok:
                        log.warning(f"Rejected satellite msg: {reason}")
                        continue

                    msg = strip_security_fields(raw_msg)
                    _dispatch(msg)

                except json.JSONDecodeError:
                    pass

        except socket.timeout:
            continue
        except OSError:
            break


# ============================================================
# INCOMING MESSAGE HANDLERS
# ============================================================

def _dispatch(msg):
    t = msg.get("type", "")

    if t == "REGISTER_ACK":
        log.info(f"Satellite confirmed ({msg.get('satellite_id')})")

    elif t == "TELEMETRY":
        _process_telemetry(msg)

    elif t == "FARM_ALERT":
        _process_farm_alert(msg)

    elif t == "ACK":
        sym = "OK" if msg.get("success") else "FAIL"
        log.info(
            f"{sym} ACK [{msg.get('turbine_id')}] "
            f"'{msg.get('action')}': {msg.get('message')}"
        )

    elif t == "ROUTE_ACK":
        if msg.get("queued"):
            log.warning(
                f"Command '{msg.get('action')}' for {msg.get('turbine_id')} QUEUED (link down)"
            )
        else:
            log.info(f"Command '{msg.get('action')}' routed to {msg.get('turbine_id')}")

    elif t == "TURBINE_BEACON":
        tid = msg.get("turbine_id")
        if tid:
            with state_lock:
                if tid not in known_turbines:
                    known_turbines[tid] = {}
            log.info(f"Beacon from {tid}")

    elif t == "DISCOVER_RESPONSE":
        turbines = msg.get("turbines", [])
        log.info(
            f"Discovery: {len(turbines)} turbine(s) | "
            f"link={'UP' if msg.get('link_up') else 'DOWN'}"
        )
        with state_lock:
            for ti in turbines:
                tid = ti.get("turbine_id")
                if tid:
                    known_turbines[tid] = ti.get("meta", known_turbines.get(tid, {}))

    elif t == "PONG":
        log.info(f"PONG from satellite - link={'UP' if msg.get('link_up') else 'DOWN'}")

    else:
        log.debug(f"Unhandled msg type: {t}")


def _process_telemetry(msg):
    """
    Store flat per-turbine telemetry and check safety thresholds.
    The satellite now sends one TELEMETRY message per turbine so there is
    nothing nested to unpack here.
    """
    global farm_wind_stopped

    tid     = msg.get("turbine_id", "?")
    sensors = msg.get("sensors", {})

    with state_lock:
        known_turbines[tid] = msg
        telemetry_history[tid].append(msg)

    # General range alerts
    for sensor, th in ALERT_THRESHOLDS.items():
        val = sensors.get(sensor)
        if val is not None and (val < th["min"] or val > th["max"]):
            log.warning(
                f"ALERT [{tid}] {sensor}={val}{th['unit']} "
                f"(safe range {th['min']} to {th['max']})"
            )

    # Per-turbine auto e-stop on critical temperature (one-shot per turbine)
    temp = sensors.get("temperature", 0)
    if temp > TEMP_CRITICAL and tid not in auto_estopped_temp:
        log.warning(f"Critical temperature on {tid} ({temp}°C) -> EMERGENCY_STOP")
        send_command(tid, "EMERGENCY_STOP", {})
        auto_estopped_temp.add(tid)

    # Clear temp flag when temperature has dropped back to safe
    if temp < TEMP_CLEAR:
        if tid in auto_estopped_temp:
            log.info(f"Temperature safe on {tid} ({temp}°C) -> RESUME")
            send_command(tid, "RESUME", {})
            auto_estopped_temp.discard(tid)

    # Farm-wide wind check (guard: only the leader's reading is authoritative)
    wind = sensors.get("wind_speed", 0)
    is_leader = msg.get("is_leader", False)

    if is_leader:
        if wind > ALERT_THRESHOLDS["wind_speed"]["max"] and not farm_wind_stopped:
            log.warning(
                f"High wind detected by leader {tid}: {wind}m/s -> "
                f"EMERGENCY_STOP ALL turbines"
            )
            send_all("EMERGENCY_STOP", {})
            farm_wind_stopped = True

        elif wind <= WIND_RESUME_THRESHOLD and farm_wind_stopped:
            log.info(
                f"Wind back to safe level ({wind}m/s) -> RESUME ALL turbines"
            )
            send_all("RESUME", {})
            farm_wind_stopped = False


def _process_farm_alert(msg):
    """
    Handle a FARM_ALERT forwarded by the satellite.
    The leader sends these when it detects extreme wind, and the satellite
    forwards them with priority. We act on them even if we already triggered
    the estop via telemetry (idempotent).
    """
    global farm_wind_stopped

    alert_type = msg.get("alert_type", "")
    wind       = msg.get("wind_speed", 0)
    leader     = msg.get("leader_id", "?")

    if alert_type == "HIGH_WIND":
        if not farm_wind_stopped:
            log.warning(
                f"FARM_ALERT HIGH_WIND from leader {leader}: "
                f"{wind}m/s -> EMERGENCY_STOP ALL turbines"
            )
            send_all("EMERGENCY_STOP", {})
            farm_wind_stopped = True
        else:
            log.info(f"FARM_ALERT HIGH_WIND received (already stopped)")
    else:
        log.warning(f"Unknown FARM_ALERT type: {alert_type}")


# ============================================================
# DISPLAY / CLI
# ============================================================

def display_status():
    print("\n" + "=" * 72)
    print(f"  GROUND CONTROL  -  {datetime.utcnow().strftime('%H:%M:%S')} UTC")
    print(f"  Satellite : {'CONNECTED' if connected_to_sat else 'DISCONNECTED'}")
    print(f"  Farm wind stopped : {farm_wind_stopped}")
    print("=" * 72)

    with state_lock:
        if not known_turbines:
            print("  No turbines yet - run: discover")

        for tid, data in known_turbines.items():
            s   = data.get("sensors",  {})
            d   = data.get("derived",  {})
            a   = data.get("actuators", {})
            st  = data.get("status",   {})

            estop  = "*** EMERGENCY STOP ***" if st.get("emergency_stop") else "Normal"
            leader = data.get("is_leader", "?")

            print(f"\n  Turbine : {tid}  [{estop}]  Leader={leader}")
            print(f"    Wind speed        : {s.get('wind_speed',   '?'):>8} m/s")
            print(f"    Power output      : {s.get('power_output', '?'):>8} kW")
            print(f"    Rotor RPM         : {s.get('rotor_rpm',    '?'):>8}")
            print(f"    Temperature       : {s.get('temperature',  '?'):>8} °C")
            print(f"    Gearbox temp      : {s.get('gearbox_temp', '?'):>8} °C")
            print(f"    Vibration         : {s.get('vibration',    '?'):>8} mm/s")
            print(f"    Hydraulic press.  : {s.get('hydraulic_pressure', '?'):>8} bar")
            print(f"    Nacelle humidity  : {s.get('nacelle_humidity',   '?'):>8} %")
            print(f"    Yaw angle         : {a.get('yaw_angle',    '?'):>8} °")
            print(f"    Blade pitch       : {a.get('blade_pitch',  '?'):>8} °")
            if d:
                print(f"    --- Derived ---")
                print(f"    Capacity factor   : {d.get('capacity_factor',   '?'):>8}")
                print(f"    Tip speed ratio   : {d.get('tip_speed_ratio',   '?'):>8}")
                print(f"    Power coefficient : {d.get('power_coefficient', '?'):>8}")
                print(f"    Mech efficiency   : {d.get('mech_efficiency',   '?'):>8}")
                print(f"    AEP projection    : {d.get('aep_projection_mwh','?'):>8} MWh")

    print("=" * 72 + "\n")


def show_history(tid, n=5):
    with state_lock:
        hist = list(telemetry_history.get(tid, []))[-n:]

    if not hist:
        print(f"No history for {tid}")
        return

    print(f"\nLast {len(hist)} readings for {tid}:")
    for e in hist:
        s = e.get("sensors", {})
        print(
            f"  [{e.get('timestamp', '?')}]  "
            f"Wind={s.get('wind_speed', '?')}m/s  "
            f"Power={s.get('power_output', '?')}kW  "
            f"RPM={s.get('rotor_rpm', '?')}  "
            f"Temp={s.get('temperature', '?')}°C"
        )
    print()


HELP = """
  help                         show this menu
  status                       show all turbine data
  discover                     find turbines via satellite
  ping                         ping the satellite
  turbines                     list known turbine IDs

  yaw    <turbine_id> <0-360>  set yaw angle
  pitch  <turbine_id> <0-90>   set blade pitch
  estop  <turbine_id>          emergency stop one turbine
  resume <turbine_id>          resume one turbine

  stopall                      emergency stop ALL turbines
  resumeall                    resume ALL turbines

  history <turbine_id>         last 5 readings
  history <turbine_id> <n>     last n readings

  quit                         exit
"""


def cli():
    time.sleep(2)
    print("\nGround Control ready. Type 'help'.\n")

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
            discover()

        elif cmd == "ping":
            ping()

        elif cmd == "turbines":
            with state_lock:
                ids = list(known_turbines.keys())
            print("Known turbines:", ids if ids else "(none - run discover)")

        elif cmd == "yaw" and len(parts) >= 3:
            try:
                send_command(parts[1], "SET_YAW", {"angle": float(parts[2])})
            except ValueError:
                print("Angle must be a number")

        elif cmd == "pitch" and len(parts) >= 3:
            try:
                send_command(parts[1], "SET_PITCH", {"pitch": float(parts[2])})
            except ValueError:
                print("Pitch must be a number")

        elif cmd == "estop" and len(parts) >= 2:
            send_command(parts[1], "EMERGENCY_STOP", {})

        elif cmd == "resume" and len(parts) >= 2:
            send_command(parts[1], "RESUME", {})

        elif cmd == "stopall":
            send_all("EMERGENCY_STOP", {})
            print("Emergency stop sent to ALL turbines")

        elif cmd == "resumeall":
            send_all("RESUME", {})
            print("Resume sent to ALL turbines")

        elif cmd == "history" and len(parts) >= 2:
            tid = parts[1]
            n   = 5
            if len(parts) >= 3:
                try:
                    n = max(1, int(parts[2]))
                except ValueError:
                    print("History count must be a number")
                    continue
            show_history(tid, n)

        elif cmd in ("quit", "exit", "q"):
            print("Goodbye.")
            sys.exit(0)

        else:
            print(f"Unknown command: '{raw}'. Type 'help'.")


# ============================================================
# MAIN
# ============================================================

def main():
    log.info("=" * 55)
    log.info(f"  GROUND CONTROL  -  {GROUND_ID}")
    log.info("=" * 55)

    threading.Thread(target=connect_loop, daemon=True).start()
    cli()


if __name__ == "__main__":
    main()