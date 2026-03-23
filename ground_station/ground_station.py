"""
CSU33D03 - Main Project 2025-26
GROUND CONTROL STATION  –  Device C  (single-machine mode: all on localhost)
"""

import socket, threading, time, json, logging, sys
from datetime import datetime
from collections import defaultdict, deque

SATELLITE_HOST = "127.0.0.1"
SATELLITE_PORT = 9001
DISC_UDP_PORT  = 9002

GROUND_ID       = "GROUND-CTRL-01"
RECONNECT_DELAY = 5
HISTORY_LEN     = 100

ALERT_THRESHOLDS = {
    "wind_speed":   {"min": 0,   "max": 25,   "unit": "m/s"},
    "power_output": {"min": 0,   "max": 2000, "unit": "kW"},
    "rotor_rpm":    {"min": 0,   "max": 20,   "unit": "RPM"},
    "temperature":  {"min": -10, "max": 70,   "unit": "°C"},
}

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [GROUND] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("ground")

telemetry_history = defaultdict(lambda: deque(maxlen=HISTORY_LEN))
known_turbines    = {}
state_lock        = threading.Lock()

_sat_sock        = None
_sock_lock       = threading.Lock()
connected_to_sat = False
_cmd_counter     = 0; _cmd_lock = threading.Lock()

def _next_id():
    global _cmd_counter
    with _cmd_lock: _cmd_counter += 1; return f"CMD-{_cmd_counter:05d}"

def send_to_sat(msg):
    with _sock_lock:
        if _sat_sock is None: log.error("Not connected to satellite"); return False
        try: _sat_sock.sendall((json.dumps(msg)+"\n").encode()); return True
        except OSError as e: log.error(f"Send failed: {e}"); return False

# ── Connection loop ───────────────────────────────────────────────────────────
def connect_loop():
    global _sat_sock, connected_to_sat
    while True:
        log.info(f"Connecting to satellite {SATELLITE_HOST}:{SATELLITE_PORT} …")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10); sock.connect((SATELLITE_HOST, SATELLITE_PORT)); sock.settimeout(5)
            with _sock_lock: _sat_sock = sock
            sock.sendall((json.dumps({"type":"REGISTER","node_type":"GROUND","ground_id":GROUND_ID,
                "timestamp":datetime.utcnow().isoformat()+"Z"})+"\n").encode())
            connected_to_sat = True; log.info("✅ Connected to satellite")
            _receive_loop(sock)
        except (ConnectionRefusedError, OSError) as e:
            log.warning(f"Cannot reach satellite: {e}  – retry in {RECONNECT_DELAY}s …")
        except Exception as e:
            log.error(f"Connection error: {e}  – retry in {RECONNECT_DELAY}s …")
        finally:
            connected_to_sat = False
            with _sock_lock: _sat_sock = None
        time.sleep(RECONNECT_DELAY)

def _receive_loop(sock):
    buffer = ""
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk: log.warning("Satellite closed connection"); break
            buffer += chunk.decode()
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1); line = line.strip()
                if not line: continue
                try: _dispatch(json.loads(line))
                except json.JSONDecodeError: pass
        except socket.timeout: continue
        except OSError: break

def _dispatch(msg):
    t = msg.get("type","")
    if   t == "REGISTER_ACK":     log.info(f"Satellite confirmed ({msg.get('satellite_id')})")
    elif t == "TELEMETRY":         _process_telemetry(msg)
    elif t == "ACK":
        sym = "✅" if msg.get("success") else "❌"
        log.info(f"{sym} ACK [{msg.get('turbine_id')}] '{msg.get('action')}': {msg.get('message')}")
    elif t == "ROUTE_ACK":
        if msg.get("queued"): log.warning(f"⚠️  Command '{msg.get('action')}' QUEUED (link down)")
        else: log.info(f"📡 Command '{msg.get('action')}' routed to {msg.get('turbine_id')}")
    elif t == "TURBINE_BEACON":
        tid = msg.get("turbine_id"); log.info(f"📡 Beacon from {tid}")
        with state_lock:
            if tid not in known_turbines: known_turbines[tid] = {}
    elif t == "DISCOVER_RESPONSE":
        turbines = msg.get("turbines",[])
        log.info(f"Discovery: {len(turbines)} turbine(s) | link={'UP' if msg.get('link_up') else 'DOWN'}")
        with state_lock:
            for ti in turbines: known_turbines[ti["turbine_id"]] = ti.get("meta",{})
    elif t == "PONG":
        log.info(f"PONG from satellite – link={'UP' if msg.get('link_up') else 'DOWN'}")

def _process_telemetry(msg):
    tid = msg.get("turbine_id","?"); sensors = msg.get("sensors",{})
    with state_lock:
        known_turbines[tid] = msg
        telemetry_history[tid].append(msg)
    log.info(f"📊 [{tid}]  Wind={sensors.get('wind_speed','?')}m/s  "
             f"Power={sensors.get('power_output','?')}kW  RPM={sensors.get('rotor_rpm','?')}  "
             f"Temp={sensors.get('temperature','?')}°C  "
             f"Yaw={msg.get('actuators',{}).get('yaw_angle','?')}°  "
             f"Pitch={msg.get('actuators',{}).get('blade_pitch','?')}°")
    for sensor, th in ALERT_THRESHOLDS.items():
        val = sensors.get(sensor)
        if val is not None and (val < th["min"] or val > th["max"]):
            log.warning(f"🚨 ALERT [{tid}] {sensor}={val}{th['unit']} (range {th['min']}–{th['max']})")
    if sensors.get("temperature", 0) > 65:
        log.warning(f"🔥 Critical temp on {tid}! Auto e-stop!")
        send_command(tid, "EMERGENCY_STOP", {})

# ── Commands ──────────────────────────────────────────────────────────────────
def send_command(turbine_id, action, params):
    cid = _next_id()
    send_to_sat({"type":"COMMAND","turbine_id":turbine_id,"ground_id":GROUND_ID,
        "cmd_id":cid,"action":action,"params":params,"timestamp":datetime.utcnow().isoformat()+"Z"})
    log.info(f"📤 {action} → {turbine_id}  (id={cid})")

def discover(): send_to_sat({"type":"DISCOVER","ground_id":GROUND_ID,"timestamp":datetime.utcnow().isoformat()+"Z"})
def ping():     send_to_sat({"type":"PING","ground_id":GROUND_ID,"timestamp":datetime.utcnow().isoformat()+"Z"})

# ── Status display ────────────────────────────────────────────────────────────
def display_status():
    print("\n" + "═"*62)
    print(f"  GROUND CONTROL  –  {datetime.utcnow().strftime('%H:%M:%S')} UTC")
    print(f"  Satellite : {'✅ CONNECTED' if connected_to_sat else '❌ DISCONNECTED'}")
    print("═"*62)
    with state_lock:
        if not known_turbines: print("  No turbines yet – run: discover")
        for tid, data in known_turbines.items():
            s = data.get("sensors",{}); a = data.get("actuators",{}); st = data.get("status",{})
            estop = "⛔ E-STOP" if st.get("emergency_stop") else "🟢 Normal"
            print(f"\n  Turbine : {tid}  [{estop}]")
            print(f"    Wind speed   : {s.get('wind_speed','?'):>8} m/s")
            print(f"    Power output : {s.get('power_output','?'):>8} kW")
            print(f"    Rotor RPM    : {s.get('rotor_rpm','?'):>8}")
            print(f"    Temperature  : {s.get('temperature','?'):>8} °C")
            print(f"    Yaw angle    : {a.get('yaw_angle','?'):>8} °")
            print(f"    Blade pitch  : {a.get('blade_pitch','?'):>8} °")
    print("═"*62 + "\n")

# ── CLI ───────────────────────────────────────────────────────────────────────
HELP = """
  status                       show all turbine data
  discover                     find turbines via satellite
  ping                         ping the satellite
  yaw    <turbine_id> <0-360>  set yaw angle
  pitch  <turbine_id> <0-90>   set blade pitch
  estop  <turbine_id>          emergency stop
  resume <turbine_id>          resume normal operation
  history <turbine_id>         last 5 readings
  turbines                     list known turbine IDs
  quit                         exit
"""

def cli():
    time.sleep(2)
    print("\n🖥️  Ground Control ready. Type 'help'.\n")
    while True:
        try: raw = input("ground> ").strip()
        except (EOFError, KeyboardInterrupt): print("\nGoodbye."); sys.exit(0)
        if not raw: continue
        parts = raw.split(); cmd = parts[0].lower()

        if   cmd == "help":     print(HELP)
        elif cmd == "status":   display_status()
        elif cmd == "discover": discover()
        elif cmd == "ping":     ping()
        elif cmd == "turbines":
            with state_lock: ids = list(known_turbines.keys())
            print("Known:", ids if ids else "(none – run discover)")
        elif cmd == "yaw" and len(parts) >= 3:
            try: send_command(parts[1], "SET_YAW", {"angle": float(parts[2])})
            except ValueError: print("Angle must be a number")
        elif cmd == "pitch" and len(parts) >= 3:
            try: send_command(parts[1], "SET_PITCH", {"pitch": float(parts[2])})
            except ValueError: print("Angle must be a number")
        elif cmd == "estop" and len(parts) >= 2:
            send_command(parts[1], "EMERGENCY_STOP", {})
        elif cmd == "resume" and len(parts) >= 2:
            send_command(parts[1], "RESUME", {})
        elif cmd == "history" and len(parts) >= 2:
            tid = parts[1]
            with state_lock: hist = list(telemetry_history.get(tid, []))[-5:]
            if not hist: print(f"No history for {tid}")
            else:
                print(f"\nLast {len(hist)} readings for {tid}:")
                for e in hist:
                    s = e.get("sensors",{})
                    print(f"  [{e.get('timestamp','?')}]  Wind={s.get('wind_speed','?')}m/s  "
                          f"Power={s.get('power_output','?')}kW  Temp={s.get('temperature','?')}°C")
        elif cmd in ("quit","exit","q"): print("Goodbye."); sys.exit(0)
        else: print(f"Unknown: '{raw}'. Type 'help'.")

def main():
    log.info("="*55 + f"\n  🖥️  GROUND CONTROL  –  {GROUND_ID}\n" + "="*55)
    threading.Thread(target=connect_loop, daemon=True).start()
    cli()

if __name__ == "__main__":
    main()"""
CSU33D03 - Main Project 2025-26
GROUND CONTROL STATION  –  Device C  (single-machine mode: all on localhost)
"""

import socket, threading, time, json, logging, sys
from datetime import datetime
from collections import defaultdict, deque

SATELLITE_HOST = "127.0.0.1"
SATELLITE_PORT = 9001
DISC_UDP_PORT  = 9002

GROUND_ID       = "GROUND-CTRL-01"
RECONNECT_DELAY = 5
HISTORY_LEN     = 100

ALERT_THRESHOLDS = {
    "wind_speed":   {"min": 0,   "max": 25,   "unit": "m/s"},
    "power_output": {"min": 0,   "max": 2000, "unit": "kW"},
    "rotor_rpm":    {"min": 0,   "max": 20,   "unit": "RPM"},
    "temperature":  {"min": -10, "max": 70,   "unit": "°C"},
}

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [GROUND] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("ground")

telemetry_history = defaultdict(lambda: deque(maxlen=HISTORY_LEN))
known_turbines    = {}
state_lock        = threading.Lock()

_sat_sock        = None
_sock_lock       = threading.Lock()
connected_to_sat = False
_cmd_counter     = 0; _cmd_lock = threading.Lock()

def _next_id():
    global _cmd_counter
    with _cmd_lock: _cmd_counter += 1; return f"CMD-{_cmd_counter:05d}"

def send_to_sat(msg):
    with _sock_lock:
        if _sat_sock is None: log.error("Not connected to satellite"); return False
        try: _sat_sock.sendall((json.dumps(msg)+"\n").encode()); return True
        except OSError as e: log.error(f"Send failed: {e}"); return False

# ── Connection loop ───────────────────────────────────────────────────────────
def connect_loop():
    global _sat_sock, connected_to_sat
    while True:
        log.info(f"Connecting to satellite {SATELLITE_HOST}:{SATELLITE_PORT} …")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10); sock.connect((SATELLITE_HOST, SATELLITE_PORT)); sock.settimeout(5)
            with _sock_lock: _sat_sock = sock
            sock.sendall((json.dumps({"type":"REGISTER","node_type":"GROUND","ground_id":GROUND_ID,
                "timestamp":datetime.utcnow().isoformat()+"Z"})+"\n").encode())
            connected_to_sat = True; log.info("✅ Connected to satellite")
            _receive_loop(sock)
        except (ConnectionRefusedError, OSError) as e:
            log.warning(f"Cannot reach satellite: {e}  – retry in {RECONNECT_DELAY}s …")
        except Exception as e:
            log.error(f"Connection error: {e}  – retry in {RECONNECT_DELAY}s …")
        finally:
            connected_to_sat = False
            with _sock_lock: _sat_sock = None
        time.sleep(RECONNECT_DELAY)

def _receive_loop(sock):
    buffer = ""
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk: log.warning("Satellite closed connection"); break
            buffer += chunk.decode()
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1); line = line.strip()
                if not line: continue
                try: _dispatch(json.loads(line))
                except json.JSONDecodeError: pass
        except socket.timeout: continue
        except OSError: break

def _dispatch(msg):
    t = msg.get("type","")
    if   t == "REGISTER_ACK":     log.info(f"Satellite confirmed ({msg.get('satellite_id')})")
    elif t == "TELEMETRY":         _process_telemetry(msg)
    elif t == "ACK":
        sym = "✅" if msg.get("success") else "❌"
        log.info(f"{sym} ACK [{msg.get('turbine_id')}] '{msg.get('action')}': {msg.get('message')}")
    elif t == "ROUTE_ACK":
        if msg.get("queued"): log.warning(f"⚠️  Command '{msg.get('action')}' QUEUED (link down)")
        else: log.info(f"📡 Command '{msg.get('action')}' routed to {msg.get('turbine_id')}")
    elif t == "TURBINE_BEACON":
        tid = msg.get("turbine_id"); log.info(f"📡 Beacon from {tid}")
        with state_lock:
            if tid not in known_turbines: known_turbines[tid] = {}
    elif t == "DISCOVER_RESPONSE":
        turbines = msg.get("turbines",[])
        log.info(f"Discovery: {len(turbines)} turbine(s) | link={'UP' if msg.get('link_up') else 'DOWN'}")
        with state_lock:
            for ti in turbines: known_turbines[ti["turbine_id"]] = ti.get("meta",{})
    elif t == "PONG":
        log.info(f"PONG from satellite – link={'UP' if msg.get('link_up') else 'DOWN'}")

def _process_telemetry(msg):
    tid = msg.get("turbine_id","?"); sensors = msg.get("sensors",{})
    with state_lock:
        known_turbines[tid] = msg
        telemetry_history[tid].append(msg)
    log.info(f"📊 [{tid}]  Wind={sensors.get('wind_speed','?')}m/s  "
             f"Power={sensors.get('power_output','?')}kW  RPM={sensors.get('rotor_rpm','?')}  "
             f"Temp={sensors.get('temperature','?')}°C  "
             f"Yaw={msg.get('actuators',{}).get('yaw_angle','?')}°  "
             f"Pitch={msg.get('actuators',{}).get('blade_pitch','?')}°")
    for sensor, th in ALERT_THRESHOLDS.items():
        val = sensors.get(sensor)
        if val is not None and (val < th["min"] or val > th["max"]):
            log.warning(f"🚨 ALERT [{tid}] {sensor}={val}{th['unit']} (range {th['min']}–{th['max']})")
    if sensors.get("temperature", 0) > 65:
        log.warning(f"🔥 Critical temp on {tid}! Auto e-stop!")
        send_command(tid, "EMERGENCY_STOP", {})

# ── Commands ──────────────────────────────────────────────────────────────────
def send_command(turbine_id, action, params):
    cid = _next_id()
    send_to_sat({"type":"COMMAND","turbine_id":turbine_id,"ground_id":GROUND_ID,
        "cmd_id":cid,"action":action,"params":params,"timestamp":datetime.utcnow().isoformat()+"Z"})
    log.info(f"📤 {action} → {turbine_id}  (id={cid})")

def discover(): send_to_sat({"type":"DISCOVER","ground_id":GROUND_ID,"timestamp":datetime.utcnow().isoformat()+"Z"})
def ping():     send_to_sat({"type":"PING","ground_id":GROUND_ID,"timestamp":datetime.utcnow().isoformat()+"Z"})

# ── Status display ────────────────────────────────────────────────────────────
def display_status():
    print("\n" + "═"*62)
    print(f"  GROUND CONTROL  –  {datetime.utcnow().strftime('%H:%M:%S')} UTC")
    print(f"  Satellite : {'✅ CONNECTED' if connected_to_sat else '❌ DISCONNECTED'}")
    print("═"*62)
    with state_lock:
        if not known_turbines: print("  No turbines yet – run: discover")
        for tid, data in known_turbines.items():
            s = data.get("sensors",{}); a = data.get("actuators",{}); st = data.get("status",{})
            estop = "⛔ E-STOP" if st.get("emergency_stop") else "🟢 Normal"
            print(f"\n  Turbine : {tid}  [{estop}]")
            print(f"    Wind speed   : {s.get('wind_speed','?'):>8} m/s")
            print(f"    Power output : {s.get('power_output','?'):>8} kW")
            print(f"    Rotor RPM    : {s.get('rotor_rpm','?'):>8}")
            print(f"    Temperature  : {s.get('temperature','?'):>8} °C")
            print(f"    Yaw angle    : {a.get('yaw_angle','?'):>8} °")
            print(f"    Blade pitch  : {a.get('blade_pitch','?'):>8} °")
    print("═"*62 + "\n")

# ── CLI ───────────────────────────────────────────────────────────────────────
HELP = """
  status                       show all turbine data
  discover                     find turbines via satellite
  ping                         ping the satellite
  yaw    <turbine_id> <0-360>  set yaw angle
  pitch  <turbine_id> <0-90>   set blade pitch
  estop  <turbine_id>          emergency stop
  resume <turbine_id>          resume normal operation
  history <turbine_id>         last 5 readings
  turbines                     list known turbine IDs
  quit                         exit
"""

def cli():
    time.sleep(2)
    print("\n🖥️  Ground Control ready. Type 'help'.\n")
    while True:
        try: raw = input("ground> ").strip()
        except (EOFError, KeyboardInterrupt): print("\nGoodbye."); sys.exit(0)
        if not raw: continue
        parts = raw.split(); cmd = parts[0].lower()

        if   cmd == "help":     print(HELP)
        elif cmd == "status":   display_status()
        elif cmd == "discover": discover()
        elif cmd == "ping":     ping()
        elif cmd == "turbines":
            with state_lock: ids = list(known_turbines.keys())
            print("Known:", ids if ids else "(none – run discover)")
        elif cmd == "yaw" and len(parts) >= 3:
            try: send_command(parts[1], "SET_YAW", {"angle": float(parts[2])})
            except ValueError: print("Angle must be a number")
        elif cmd == "pitch" and len(parts) >= 3:
            try: send_command(parts[1], "SET_PITCH", {"pitch": float(parts[2])})
            except ValueError: print("Angle must be a number")
        elif cmd == "estop" and len(parts) >= 2:
            send_command(parts[1], "EMERGENCY_STOP", {})
        elif cmd == "resume" and len(parts) >= 2:
            send_command(parts[1], "RESUME", {})
        elif cmd == "history" and len(parts) >= 2:
            tid = parts[1]
            with state_lock: hist = list(telemetry_history.get(tid, []))[-5:]
            if not hist: print(f"No history for {tid}")
            else:
                print(f"\nLast {len(hist)} readings for {tid}:")
                for e in hist:
                    s = e.get("sensors",{})
                    print(f"  [{e.get('timestamp','?')}]  Wind={s.get('wind_speed','?')}m/s  "
                          f"Power={s.get('power_output','?')}kW  Temp={s.get('temperature','?')}°C")
        elif cmd in ("quit","exit","q"): print("Goodbye."); sys.exit(0)
        else: print(f"Unknown: '{raw}'. Type 'help'.")

def main():
    log.info("="*55 + f"\n  🖥️  GROUND CONTROL  –  {GROUND_ID}\n" + "="*55)
    threading.Thread(target=connect_loop, daemon=True).start()
    cli()

if __name__ == "__main__":
    main()
"""
CSU33D03 - Main Project 2025-26
LEO SATELLITE RELAY  –  Device B  (single-machine mode: all on localhost)
"""

import socket, threading, time, random, json, logging, sys, queue
from datetime import datetime
from collections import defaultdict

TURBINE_LISTEN_PORT = 9000
GROUND_LISTEN_PORT  = 9001
DISCOVERY_UDP_PORT  = 9002

PROP_DELAY_MS      = 25.0
PACKET_LOSS_RATE   = 0.03
VISIBILITY_CYCLE_S = 90 * 60
VISIBILITY_WINDOW_S= 10 * 60
TIME_SCALE         = 60

SATELLITE_ID = "LEO-SAT-01"

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [SATELLITE] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("satellite")

turbine_connections = {}; ground_connections = {}
t_lock = threading.Lock(); g_lock = threading.Lock()
relay_queue   = queue.Queue(maxsize=300)
command_queue = defaultdict(lambda: queue.Queue(maxsize=100))
link_up = True

# ── Channel helpers ───────────────────────────────────────────────────────────
def channel_delay(): time.sleep((PROP_DELAY_MS/1000.0) + abs(random.gauss(0, 0.003)))
def channel_loss():  return random.random() < PACKET_LOSS_RATE

# ── Visibility manager ────────────────────────────────────────────────────────
def visibility_manager():
    global link_up
    cycle = VISIBILITY_CYCLE_S / TIME_SCALE; window = VISIBILITY_WINDOW_S / TIME_SCALE
    while True:
        link_up = True;  log.info(f"🛰️  LINK UP   ({window:.0f}s window at ×{TIME_SCALE})"); time.sleep(window)
        link_up = False; log.info(f"🛰️  LINK DOWN ({cycle-window:.0f}s blackout)");           time.sleep(cycle - window)

# ── Turbine handler ───────────────────────────────────────────────────────────
def turbine_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", TURBINE_LISTEN_PORT)); srv.listen(20)
        log.info(f"  Turbine uplink  → 0.0.0.0:{TURBINE_LISTEN_PORT}")
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle_turbine, args=(conn, addr), daemon=True).start()

def handle_turbine(conn, addr):
    turbine_id = None; buffer = ""
    try:
        conn.settimeout(30)
        with conn:
            while True:
                chunk = conn.recv(4096)
                if not chunk: break
                buffer += chunk.decode()
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1); line = line.strip()
                    if not line: continue
                    try: msg = json.loads(line)
                    except json.JSONDecodeError: continue
                    t = msg.get("type","")
                    if t == "REGISTER":
                        turbine_id = msg.get("turbine_id", str(addr))
                        with t_lock: turbine_connections[turbine_id] = {"sock": conn, "meta": msg}
                        log.info(f"✅ Turbine registered: {turbine_id}")
                        conn.sendall((json.dumps({"type":"REGISTER_ACK","satellite_id":SATELLITE_ID,
                            "turbine_id":turbine_id,"timestamp":datetime.utcnow().isoformat()+"Z"})+"\n").encode())
                    elif t == "TELEMETRY":
                        if not link_up or channel_loss(): continue
                        msg.update({"relayed_by":SATELLITE_ID,"relay_timestamp":datetime.utcnow().isoformat()+"Z"})
                        try: relay_queue.put_nowait(json.dumps(msg))
                        except queue.Full: pass
                    elif t == "ACK":
                        try: relay_queue.put_nowait(json.dumps(msg))
                        except queue.Full: pass
                    elif t == "BEACON":
                        _broadcast_ground(json.dumps({"type":"TURBINE_BEACON","turbine_id":turbine_id,
                            "satellite":SATELLITE_ID,"timestamp":datetime.utcnow().isoformat()+"Z"}))
                    if turbine_id:
                        cq = command_queue[turbine_id]
                        while not cq.empty():
                            try: channel_delay(); conn.sendall((cq.get_nowait()+"\n").encode())
                            except (queue.Empty, OSError): break
    except Exception as e: log.info(f"Turbine {turbine_id or addr} disconnected: {e}")
    finally:
        if turbine_id:
            with t_lock: turbine_connections.pop(turbine_id, None)

# ── Ground handler ────────────────────────────────────────────────────────────
def ground_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", GROUND_LISTEN_PORT)); srv.listen(20)
        log.info(f"  Ground downlink → 0.0.0.0:{GROUND_LISTEN_PORT}")
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle_ground, args=(conn, addr), daemon=True).start()

def handle_ground(conn, addr):
    ground_id = None; buffer = ""
    try:
        conn.settimeout(60)
        with conn:
            while True:
                chunk = conn.recv(4096)
                if not chunk: break
                buffer += chunk.decode()
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1); line = line.strip()
                    if not line: continue
                    try: msg = json.loads(line)
                    except json.JSONDecodeError: continue
                    t = msg.get("type","")
                    if t == "REGISTER":
                        ground_id = msg.get("ground_id", str(addr))