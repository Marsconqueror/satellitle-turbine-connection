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
                        with g_lock: ground_connections[ground_id] = {"sock": conn}
                        log.info(f"✅ Ground registered: {ground_id}")
                        conn.sendall((json.dumps({"type":"REGISTER_ACK","satellite_id":SATELLITE_ID,
                            "ground_id":ground_id,"timestamp":datetime.utcnow().isoformat()+"Z"})+"\n").encode())
                    elif t == "COMMAND":
                        target = msg.get("turbine_id")
                        msg.update({"routed_via":SATELLITE_ID,"route_timestamp":datetime.utcnow().isoformat()+"Z"})
                        payload = json.dumps(msg)
                        queued = not link_up or channel_loss()
                        if queued:
                            command_queue[target].put(payload)
                            log.warning(f"Command queued for {target} (link={'down' if not link_up else 'loss'})")
                        else:
                            with t_lock: tc = turbine_connections.get(target)
                            if tc:
                                try: channel_delay(); tc["sock"].sendall((payload+"\n").encode())
                                except OSError: command_queue[target].put(payload); queued=True
                            else: command_queue[target].put(payload); queued=True
                        _route_ack(conn, msg, queued)
                    elif t == "DISCOVER":
                        with t_lock:
                            known = [{"turbine_id":tid,"meta":info["meta"]} for tid,info in turbine_connections.items()]
                        conn.sendall((json.dumps({"type":"DISCOVER_RESPONSE","satellite_id":SATELLITE_ID,
                            "turbines":known,"link_up":link_up,"timestamp":datetime.utcnow().isoformat()+"Z"})+"\n").encode())
                    elif t == "PING":
                        conn.sendall((json.dumps({"type":"PONG","satellite_id":SATELLITE_ID,
                            "link_up":link_up,"timestamp":datetime.utcnow().isoformat()+"Z"})+"\n").encode())
    except Exception as e: log.info(f"Ground {ground_id or addr} disconnected: {e}")
    finally:
        if ground_id:
            with g_lock: ground_connections.pop(ground_id, None)

def _route_ack(conn, orig, queued):
    try: conn.sendall((json.dumps({"type":"ROUTE_ACK","turbine_id":orig.get("turbine_id"),
        "action":orig.get("action"),"queued":queued,"satellite":SATELLITE_ID,
        "timestamp":datetime.utcnow().isoformat()+"Z"})+"\n").encode())
    except OSError: pass

def relay_loop():
    while True:
        try: _broadcast_ground(relay_queue.get(timeout=1))
        except queue.Empty: pass

def _broadcast_ground(payload):
    channel_delay()
    with g_lock:
        dead = []
        for gid, info in ground_connections.items():
            try: info["sock"].sendall((payload+"\n").encode())
            except OSError: dead.append(gid)
        for gid in dead: ground_connections.pop(gid, None)

def udp_discovery():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind(("0.0.0.0", DISCOVERY_UDP_PORT))
        log.info(f"  UDP discovery   → 0.0.0.0:{DISCOVERY_UDP_PORT}")
        while True:
            try:
                data, addr = udp.recvfrom(512)
                msg = json.loads(data.decode())
                if msg.get("type") == "DISCOVER_UDP":
                    udp.sendto(json.dumps({"type":"SATELLITE_INFO","satellite_id":SATELLITE_ID,
                        "turbine_port":TURBINE_LISTEN_PORT,"ground_port":GROUND_LISTEN_PORT,
                        "link_up":link_up,"timestamp":datetime.utcnow().isoformat()+"Z"}).encode(), addr)
            except Exception: pass

def status_printer():
    while True:
        time.sleep(15)
        with t_lock: tc = len(turbine_connections)
        with g_lock: gc = len(ground_connections)
        log.info(f"Status | link={'UP  ' if link_up else 'DOWN'} | turbines={tc} | ground={gc} | relay_q={relay_queue.qsize()}")

def main():
    log.info("="*55 + f"\n  🛰️  SATELLITE  –  {SATELLITE_ID}\n" + "="*55)
    for target in [turbine_listener, ground_listener, udp_discovery, relay_loop, visibility_manager, status_printer]:
        threading.Thread(target=target, daemon=True).start()
    log.info("All services running. Ctrl+C to stop.\n")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: log.info("Satellite shutting down.")

if __name__ == "__main__":
    main()