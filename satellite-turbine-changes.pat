diff --git a/README.md b/README.md
index fadefa0..f3fd6db 100644
--- a/README.md
+++ b/README.md
@@ -64,9 +64,23 @@ The default farm list is `TURBINE-01`, `TURBINE-02`, and `TURBINE-03`.
 
 ## Running On Different Computers
 
-Find the IP address of the computer running `satellite.py`.
+You can also run the three parts on three different computers if all of them are on the same Wi-Fi or LAN.
 
-Then set `SATELLITE_HOST` before starting the turbine and ground station.
+Use the computers like this:
+
+- Computer 1 runs the satellite.
+- Computer 2 runs the turbine.
+- Computer 3 runs the ground station.
+
+First, find the IP address of Computer 1, because that is where `satellite.py` is running.
+
+On Computer 1:
+
+```bash
+python satellite/satellite.py
+```
+
+On Computer 2, set `SATELLITE_HOST` to Computer 1's IP address, then start the turbine.
 
 PowerShell example:
 
@@ -75,14 +89,22 @@ $env:SATELLITE_HOST="192.168.1.50"
 python turbine/turbine.py TURBINE-01
 ```
 
-In another terminal:
+On Computer 3, use the same satellite IP address, then start the ground station.
 
 ```powershell
 $env:SATELLITE_HOST="192.168.1.50"
 python ground_station/ground_station.py
 ```
 
-Use `127.0.0.1` when all programs run on the same computer.
+In this example, `192.168.1.50` is only an example. Replace it with the real IP address of the satellite computer.
+
+Use `127.0.0.1` only when all programs run on the same computer.
+
+If the computers cannot connect, check that:
+
+- all three computers are on the same network
+- the satellite program is running first
+- the firewall is not blocking ports `9000`, `9001`, and `9002`
 
 ## Files
 
diff --git a/channel.py b/channel.py
index f293304..8565d13 100644
--- a/channel.py
+++ b/channel.py
@@ -1,14 +1,8 @@
-"""
-channel.py
-Faster + demo-friendly LEO channel simulation
-- shorter delays
-- link UP most of the time
-- brief outages instead of long ones
-"""
+"""Small satellite channel simulation."""
 
 import time, random, threading
 
-# These values make the simulated satellite link feel realistic but still quick.
+# Basic values for the fake satellite link.
 BASE_DELAY_MS = 120        # normal latency
 JITTER_MS     = 80         # random variation
 LOSS_PROB     = 0.03       # 3% theoretical packet loss
@@ -16,7 +10,7 @@ UP_TIME_S     = 45         # link stays up for 45 sec
 DOWN_TIME_S   = 8          # link down only 8 sec
 
 _link_up = True
-# Shared channel statistics used by the satellite status screen.
+# Values shown in satellite status.
 _stats = {
     "delay_samples": [],
     "packets_total": 0,
@@ -26,7 +20,7 @@ _stats = {
 _lock = threading.Lock()
 
 def channel_delay():
-    """Sleep for a short simulated satellite delay."""
+    """Add a small delay like a satellite link."""
     delay_ms = BASE_DELAY_MS + random.randint(0, JITTER_MS)
     time.sleep(delay_ms / 1000.0)
     with _lock:
@@ -38,7 +32,7 @@ def channel_delay():
         )
 
 def channel_loss():
-    """Randomly decide whether a packet is lost."""
+    """Randomly drop some packets."""
     lost = random.random() < LOSS_PROB
     with _lock:
         _stats["packets_total"] += 1
@@ -47,11 +41,11 @@ def channel_loss():
     return lost
 
 def is_link_up():
-    """Return whether the satellite link is currently visible."""
+    """Check if the link is up."""
     return _link_up
 
 def visibility_manager():
-    """Switch the simulated link between up and down windows."""
+    """Switch the link between up and down."""
     global _link_up
     while True:
         _link_up = True
@@ -60,7 +54,7 @@ def visibility_manager():
         time.sleep(DOWN_TIME_S)
 
 def get_stats():
-    """Return the latest delay and packet-loss statistics."""
+    """Return simple channel statistics."""
     with _lock:
         total = _stats["packets_total"]
         lost = _stats["packets_lost"]
diff --git a/docs/protocol.md b/docs/protocol.md
index ede88b3..48737fb 100644
--- a/docs/protocol.md
+++ b/docs/protocol.md
@@ -27,6 +27,27 @@ For local testing, the turbine and ground station use `127.0.0.1`.
 
 For network testing, set `SATELLITE_HOST` to the satellite computer IP address.
 
+## Why TCP And UDP Are Both Used
+
+This project uses TCP for the main communication.
+
+TCP is used for:
+
+- turbine sensor data going to the satellite
+- satellite data going to the ground station
+- ground station commands going back to the turbine
+
+TCP was chosen for these messages because the connection is more reliable. Commands like `EMERGENCY_STOP`, `SET_YAW`, and `SET_PITCH` should not be lost easily.
+
+UDP is only used for simple discovery on port `9002`.
+
+UDP was chosen for discovery because it is lightweight. A small discovery message can be sent without opening a full TCP connection first. If a UDP discovery message is missed, the program can just try again.
+
+So the simple idea is:
+
+- TCP is used for important messages after the devices are connected.
+- UDP is used for quick discovery messages.
+
 ## Message Types
 
 `REGISTER`
diff --git a/equipment.py b/equipment.py
index f81baac..5a33355 100644
--- a/equipment.py
+++ b/equipment.py
@@ -1,39 +1,10 @@
-"""
-CSU33D03 - Main Project 2025-26
-Group 9 - Physical Equipment Models
-
-Models the physical components of a wind turbine.
-Each class tracks its own state and health, and produces realistic
-sensor readings based on operating conditions.
-
-Components modelled:
-  - Rotor       blades, pitch control, tip speed ratio
-  - Gearbox     temperature, oil pressure, wear factor
-  - Generator   temperature, output power, efficiency
-  - Nacelle     yaw system, humidity, vibration
-  - HydraulicSystem  blade pitch actuator pressure
-  - Tower       base vibration, structural health
-
-Imported by sensors.py to produce physically-consistent readings.
-
-Usage:
-    from equipment import Rotor, Gearbox, Generator, Nacelle, HydraulicSystem
-    rotor     = Rotor()
-    gearbox   = Gearbox()
-    generator = Generator()
-    nacelle   = Nacelle()
-    hydraulics = HydraulicSystem()
-"""
+"""Simple wind turbine equipment models."""
 
 import math, random, time
 
 
 class Rotor:
-    """
-    Models the rotor assembly: three blades, hub, pitch actuators.
-    Blade pitch (0-90 deg) controls how much wind energy is captured.
-    0 deg = full power, 90 deg = feathered (stopped).
-    """
+    """Model the turbine rotor."""
     def __init__(self):
         self.blade_pitch   = 15.0    # degrees
         self.diameter_m    = 80.0    # rotor diameter in metres
@@ -45,7 +16,7 @@ class Rotor:
         self.blade_pitch = max(0.0, min(90.0, degrees))
 
     def rpm(self, wind_speed: float) -> float:
-        """Calculate rotor RPM from wind speed and blade pitch."""
+        """Calculate rotor speed from wind and pitch."""
         if wind_speed < 3.0 or self.blade_pitch > 85.0:
             return 0.0
         pitch_factor = 1.0 - (self.blade_pitch / 90.0)
@@ -54,32 +25,29 @@ class Rotor:
         return max(0.0, round(base_rpm + noise, 2))
 
     def tip_speed_ratio(self, wind_speed: float) -> float:
-        """TSR = blade tip speed / wind speed. Optimal ~7 for most turbines."""
+        """Calculate blade tip speed compared to wind speed."""
         rpm = self.rpm(wind_speed)
         tip_speed = (rpm * 2 * math.pi / 60) * self.radius_m
         return round(tip_speed / max(wind_speed, 0.1), 2)
 
     def power_coefficient(self, wind_speed: float) -> float:
-        """Aerodynamic efficiency. Betz limit = 0.593."""
+        """Calculate simple rotor efficiency."""
         tsr = self.tip_speed_ratio(wind_speed)
         cp  = 0.22 * (0.8 * tsr - 0.035 * tsr**3 + 0.003)
         return round(max(0.0, min(0.593, cp)), 4)
 
     def vibration(self) -> float:
-        """Rotor vibration in mm/s. Increases with wear and imbalance."""
+        """Return rotor vibration."""
         base = 1.2 + self._wear * 3.0
         return round(max(0.1, base + random.gauss(0, 0.1)), 2)
 
     def age(self, hours: float = 1.0):
-        """Simulate wear accumulation over operating hours."""
+        """Add a small amount of rotor wear."""
         self._wear = min(1.0, self._wear + hours * 0.000001)
 
 
 class Gearbox:
-    """
-    Models the gearbox: converts low-speed rotor rotation to
-    high-speed generator input. Main heat source in the drivetrain.
-    """
+    """Model the turbine gearbox."""
     def __init__(self):
         self.gear_ratio  = 100.0    # rotor RPM * gear_ratio = generator RPM
         self._temp_c     = 45.0     # internal temperature
@@ -87,10 +55,10 @@ class Gearbox:
         self._wear       = 0.0
 
     def update(self, rotor_rpm: float, ambient_temp: float = 15.0):
-        """Update gearbox temperature based on load."""
+        """Update gearbox temperature."""
         load_heat  = (rotor_rpm / 15.0) * 35.0
         target     = ambient_temp + load_heat + self._wear * 10.0
-        # temperature lags behind load (thermal mass)
+        # Temperature changes slowly.
         self._temp_c += (target - self._temp_c) * 0.04
         self._temp_c += random.gauss(0, 0.3)
 
@@ -99,12 +67,12 @@ class Gearbox:
         return round(self._temp_c, 1)
 
     def oil_pressure_bar(self) -> float:
-        """Oil pressure drops as oil degrades."""
+        """Return oil pressure."""
         base = 4.5 - self._oil_degr * 1.5
         return round(max(1.0, base + random.gauss(0, 0.05)), 2)
 
     def efficiency(self) -> float:
-        """Mechanical efficiency, degrades with wear."""
+        """Return gearbox efficiency."""
         return round(max(0.88, 0.98 - self._wear * 0.08), 3)
 
     def age(self, hours: float = 1.0):
@@ -114,10 +82,7 @@ class Gearbox:
 
 
 class Generator:
-    """
-    Models the electrical generator. Converts mechanical power to electricity.
-    Temperature is the primary health indicator.
-    """
+    """Model the electrical generator."""
     RATED_KW = 2000.0
 
     def __init__(self):
@@ -126,7 +91,7 @@ class Generator:
         self._wear        = 0.0
 
     def update(self, rotor_rpm: float, wind_speed: float, ambient_temp: float = 15.0):
-        """Update generator temperature and output based on wind conditions."""
+        """Update generator temperature."""
         power   = self.power_output_kw(wind_speed)
         load    = power / self.RATED_KW
         target  = ambient_temp + 15.0 + load * 35.0 + self._wear * 5.0
@@ -138,7 +103,7 @@ class Generator:
         return round(self._temp_c, 1)
 
     def power_output_kw(self, wind_speed: float) -> float:
-        """Power from wind using simplified kinetic energy formula."""
+        """Estimate power output from wind speed."""
         rho  = 1.225   # air density kg/m3
         area = math.pi * (40 ** 2)
         raw  = 0.5 * rho * area * (wind_speed ** 3) / 1000.0
@@ -149,7 +114,7 @@ class Generator:
         return round(self.power_output_kw(wind_speed) / self.RATED_KW, 3)
 
     def aep_projection_mwh(self, wind_speed: float) -> float:
-        """Projected annual energy at current output."""
+        """Estimate yearly energy from current output."""
         return round(self.power_output_kw(wind_speed) * 8760 / 1000, 1)
 
     def age(self, hours: float = 1.0):
@@ -159,10 +124,7 @@ class Generator:
 
 
 class Nacelle:
-    """
-    Models the nacelle housing: contains the gearbox, generator, and yaw system.
-    Tracks yaw angle, internal humidity and structural vibration.
-    """
+    """Model the nacelle."""
     def __init__(self):
         self.yaw_angle  = 180.0   # degrees, 0=North, 180=South
         self._humidity  = 55.0    # % relative humidity inside nacelle
@@ -173,24 +135,20 @@ class Nacelle:
         self.yaw_angle = max(0.0, min(360.0, degrees))
 
     def humidity(self) -> float:
-        """Nacelle humidity drifts slowly."""
+        """Return nacelle humidity."""
         self._humidity += random.gauss(0, 0.3)
         self._humidity  = max(30.0, min(90.0, self._humidity))
         return round(self._humidity, 1)
 
     def vibration(self, rotor_vib: float) -> float:
-        """Nacelle vibration tracks rotor vibration with damping."""
+        """Return nacelle vibration."""
         self._vib += (rotor_vib - self._vib) * 0.1
         self._vib += random.gauss(0, 0.05)
         return round(max(0.1, self._vib), 2)
 
 
 class HydraulicSystem:
-    """
-    Models the hydraulic system used to actuate blade pitch changes.
-    Pressure must be maintained for pitch control to work.
-    Low pressure = pitch control failure risk.
-    """
+    """Model the blade pitch hydraulic system."""
     NOMINAL_BAR = 180.0
     MAX_BAR     = 280.0
     MIN_BAR     = 120.0
@@ -200,11 +158,11 @@ class HydraulicSystem:
         self._leak_rate = 0.0    # simulates slow leak developing over time
 
     def update(self, pitch_changing: bool = False):
-        """Pressure drops when pitch is being adjusted, recovers via pump."""
+        """Update hydraulic pressure."""
         if pitch_changing:
             self._pressure -= random.uniform(2.0, 5.0)
         else:
-            # pump restores pressure
+            # Pump brings pressure back up.
             self._pressure += (self.NOMINAL_BAR - self._pressure) * 0.08
         self._pressure -= self._leak_rate
         self._pressure += random.gauss(0, 1.2)
@@ -221,5 +179,5 @@ class HydraulicSystem:
         return "OK"
 
     def develop_leak(self):
-        """Simulate a slow leak developing - useful for demo fault injection."""
+        """Start a small simulated leak."""
         self._leak_rate = 0.5
diff --git a/ground_station/ground_station.py b/ground_station/ground_station.py
index 097de4f..9799d31 100644
--- a/ground_station/ground_station.py
+++ b/ground_station/ground_station.py
@@ -1,18 +1,4 @@
-"""
-CSU33D03 - Main Project 2025-26
-GROUND CONTROL STATION  -  Device C
-
-This is the human operator side of the system. It connects to the satellite,
-receives live telemetry from wind turbines, displays their status, and lets
-the operator send control commands like changing yaw, pitch or triggering an
-emergency stop.
-
-Main features:
-- Checks message signatures before using incoming data.
-- Stores live telemetry for each turbine.
-- Sends commands like yaw, pitch, emergency stop, and resume.
-- Stops turbines automatically if wind or temperature becomes unsafe.
-"""
+"""Ground station program for viewing turbines and sending commands."""
 
 import socket, threading, time, json, logging, sys, os
 from datetime import datetime
@@ -21,8 +7,8 @@ from collections import defaultdict, deque
 sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
 from security import sign_message, verify_message, strip_security_fields
 
-# Use localhost for easy testing on one computer.
-# For two computers, set SATELLITE_HOST to the satellite computer IP address.
+# Use localhost when everything runs on one computer.
+# Change SATELLITE_HOST when the satellite is on another computer.
 SATELLITE_HOST = os.getenv("SATELLITE_HOST", "127.0.0.1")
 SATELLITE_PORT = int(os.getenv("SATELLITE_GROUND_PORT", "9001"))
 GROUND_ID      = os.getenv("GROUND_ID", "GROUND-CTRL-01")
@@ -41,9 +27,9 @@ ALERT_THRESHOLDS = {
     "nacelle_humidity":   {"min": 0,    "max": 85,   "unit": "%"},
 }
 
-WIND_RESUME_THRESHOLD = 20.0   # m/s — farm resumes below this after a wind estop
-TEMP_CRITICAL         = 65.0   # °C  — individual turbine auto estop
-TEMP_CLEAR            = 60.0   # °C  — temperature back to safe, clear the flag
+WIND_RESUME_THRESHOLD = 20.0   # resume farm below this wind speed
+TEMP_CRITICAL         = 65.0   # stop one turbine above this temperature
+TEMP_CLEAR            = 60.0   # resume when temperature is safe again
 
 logging.basicConfig(
     level=logging.INFO,
@@ -60,9 +46,9 @@ _sat_sock        = None
 _sock_lock       = threading.Lock()
 connected_to_sat = False
 
-# Track auto-stops so we don't spam commands every telemetry packet
-auto_estopped_temp = set()    # turbine IDs stopped due to temperature
-farm_wind_stopped  = False    # True when the whole farm was stopped for high wind
+# Track auto-stops so commands are not repeated every time.
+auto_estopped_temp = set()    # turbines stopped due to temperature
+farm_wind_stopped  = False    # whole farm stopped due to high wind
 
 _cmd_counter = 0
 _cmd_lock    = threading.Lock()
@@ -74,9 +60,6 @@ def _next_id():
         return f"CMD-{_cmd_counter:05d}"
 
 
-# ============================================================
-# OUTGOING MESSAGES
-# ============================================================
 
 def send_to_sat(msg):
     """Sign a message and send it to the satellite."""
@@ -111,11 +94,7 @@ def send_command(turbine_id, action, params):
 
 
 def send_all(action, params=None):
-    """
-    Broadcast the same command to ALL turbines via a single ALL-targeted message.
-    The satellite will fan it out to every connected turbine in one go, which
-    guarantees consistent state (e.g. all blades go to 90° on stopall).
-    """
+    """Send the same command to all turbines."""
     if params is None:
         params = {}
     if not connected_to_sat:
@@ -142,9 +121,6 @@ def ping():
     })
 
 
-# ============================================================
-# CONNECTION / RECEIVE LOOP
-# ============================================================
 
 def connect_loop():
     """Keep trying to connect to the satellite if the link drops."""
@@ -206,7 +182,7 @@ def _receive_loop(sock):
                 try:
                     raw_msg = json.loads(line)
 
-                    # verify_message returns a (bool, reason) tuple
+                    # Check the message before using it.
                     ok, reason = verify_message(raw_msg)
                     if not ok:
                         log.warning(f"Rejected satellite msg: {reason}")
@@ -224,9 +200,6 @@ def _receive_loop(sock):
             break
 
 
-# ============================================================
-# INCOMING MESSAGE HANDLERS
-# ============================================================
 
 def _dispatch(msg):
     """Send each incoming message type to the right handler."""
@@ -284,11 +257,7 @@ def _dispatch(msg):
 
 
 def _process_telemetry(msg):
-    """
-    Store flat per-turbine telemetry and check safety thresholds.
-    The satellite now sends one TELEMETRY message per turbine so there is
-    nothing nested to unpack here.
-    """
+    """Store turbine data and check for unsafe values."""
     global farm_wind_stopped
 
     tid     = msg.get("turbine_id", "?")
@@ -298,7 +267,7 @@ def _process_telemetry(msg):
         known_turbines[tid] = msg
         telemetry_history[tid].append(msg)
 
-    # General range alerts
+    # Print a warning if a sensor is outside the safe range.
     for sensor, th in ALERT_THRESHOLDS.items():
         val = sensors.get(sensor)
         if val is not None and (val < th["min"] or val > th["max"]):
@@ -307,21 +276,21 @@ def _process_telemetry(msg):
                 f"(safe range {th['min']} to {th['max']})"
             )
 
-    # Per-turbine auto e-stop on critical temperature (one-shot per turbine)
+    # Stop one turbine if its temperature is too high.
     temp = sensors.get("temperature", 0)
     if temp > TEMP_CRITICAL and tid not in auto_estopped_temp:
         log.warning(f"Critical temperature on {tid} ({temp}°C) -> EMERGENCY_STOP")
         send_command(tid, "EMERGENCY_STOP", {})
         auto_estopped_temp.add(tid)
 
-    # Clear temp flag when temperature has dropped back to safe
+    # Resume the turbine when the temperature becomes safe.
     if temp < TEMP_CLEAR:
         if tid in auto_estopped_temp:
             log.info(f"Temperature safe on {tid} ({temp}°C) -> RESUME")
             send_command(tid, "RESUME", {})
             auto_estopped_temp.discard(tid)
 
-    # Farm-wide wind check (guard: only the leader's reading is authoritative)
+    # The leader checks the wind for the whole farm.
     wind = sensors.get("wind_speed", 0)
     is_leader = msg.get("is_leader", False)
 
@@ -343,12 +312,7 @@ def _process_telemetry(msg):
 
 
 def _process_farm_alert(msg):
-    """
-    Handle a FARM_ALERT forwarded by the satellite.
-    The leader sends these when it detects extreme wind, and the satellite
-    forwards them with priority. We act on them even if we already triggered
-    the estop via telemetry (idempotent).
-    """
+    """Handle a high wind alert from the leader turbine."""
     global farm_wind_stopped
 
     alert_type = msg.get("alert_type", "")
@@ -369,9 +333,6 @@ def _process_farm_alert(msg):
         log.warning(f"Unknown FARM_ALERT type: {alert_type}")
 
 
-# ============================================================
-# DISPLAY / CLI
-# ============================================================
 
 def display_status():
     """Print the latest turbine values in the terminal."""
@@ -540,9 +501,6 @@ def cli():
             print(f"Unknown command: '{raw}'. Type 'help'.")
 
 
-# ============================================================
-# MAIN
-# ============================================================
 
 def main():
     log.info("=" * 55)
diff --git a/satellite/satellite.py b/satellite/satellite.py
index 78b0a00..596b02e 100644
--- a/satellite/satellite.py
+++ b/satellite/satellite.py
@@ -1,17 +1,4 @@
-"""
-CSU33D03 - Main Project 2025-26
-LEO SATELLITE RELAY  -  Device B
-
-This script acts as the satellite relay sitting between the wind turbine
-and the ground control station. It simulates a real Low Earth Orbit satellite
-by adding realistic communication delays, packet loss, and visibility windows.
-
-Main features:
-- Accepts turbine and ground station connections.
-- Relays turbine telemetry to the ground station.
-- Routes ground station commands back to turbines.
-- Simulates satellite delay, packet loss, and link visibility.
-"""
+"""Satellite relay between the turbine and ground station."""
 
 import socket, threading, time, random, json, logging, sys, os, queue
 from datetime import datetime
@@ -45,11 +32,8 @@ _seq_tracker = {}
 _stats_lock  = threading.Lock()
 
 
-# ============================================================
-# TURBINE LISTENER
-# ============================================================
 def turbine_listener():
-    """Listen for turbine TCP connections."""
+    """Wait for turbine connections."""
     with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
         srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
         srv.bind(("0.0.0.0", TURBINE_LISTEN_PORT))
@@ -62,7 +46,7 @@ def turbine_listener():
 
 
 def handle_turbine(conn, addr):
-    """Handle one connected turbine."""
+    """Receive messages from one turbine."""
     turbine_id = None
     buffer     = ""
     try:
@@ -109,10 +93,10 @@ def handle_turbine(conn, addr):
                         conn.sendall((json.dumps(ack) + "\n").encode())
 
                     elif t == "TELEMETRY":
-                        # Each message is now a flat per-turbine payload — forward as-is.
+                        # Forward one turbine telemetry message.
                         _track_seq(msg)
                         if not is_link_up() or channel_loss():
-                            pass  # drop on simulated loss (still drain the command queue below)
+                            pass  # drop packet during simulated loss
                         else:
                             msg["relayed_by"]      = SATELLITE_ID
                             msg["relay_timestamp"] = datetime.utcnow().isoformat() + "Z"
@@ -122,7 +106,7 @@ def handle_turbine(conn, addr):
                                 log.warning("Relay queue full - dropping telemetry")
 
                     elif t == "FARM_ALERT":
-                        # Safety-critical: forward immediately, bypass channel loss simulation.
+                        # Forward important farm alerts without dropping them.
                         msg["relayed_by"]      = SATELLITE_ID
                         msg["relay_timestamp"] = datetime.utcnow().isoformat() + "Z"
                         log.warning(
@@ -132,7 +116,7 @@ def handle_turbine(conn, addr):
                         try:
                             relay_queue.put_nowait(json.dumps(sign_message(msg)))
                         except queue.Full:
-                            # Priority: clear a slot and insert
+                            # Clear one old item if the queue is full.
                             try:
                                 relay_queue.get_nowait()
                             except queue.Empty:
@@ -154,7 +138,7 @@ def handle_turbine(conn, addr):
                         })
                         _broadcast_ground(json.dumps(beacon))
 
-                    # Drain any queued commands for this turbine
+                    # Send any commands waiting for this turbine.
                     if turbine_id:
                         cq = command_queue[turbine_id]
                         while not cq.empty():
@@ -173,7 +157,7 @@ def handle_turbine(conn, addr):
 
 
 def _track_seq(msg):
-    """Track sequence numbers so missing telemetry can be logged."""
+    """Check if telemetry packets were missed."""
     tid = msg.get("turbine_id")
     seq = msg.get("seq")
     if not tid or seq is None:
@@ -190,11 +174,8 @@ def _track_seq(msg):
         _seq_tracker[tid] = seq
 
 
-# ============================================================
-# GROUND LISTENER
-# ============================================================
 def ground_listener():
-    """Listen for ground station TCP connections."""
+    """Wait for ground station connections."""
     with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
         srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
         srv.bind(("0.0.0.0", GROUND_LISTEN_PORT))
@@ -207,7 +188,7 @@ def ground_listener():
 
 
 def handle_ground(conn, addr):
-    """Handle one connected ground station."""
+    """Receive messages from one ground station."""
     ground_id = None
     buffer    = ""
     try:
@@ -289,7 +270,7 @@ def handle_ground(conn, addr):
 
 
 def _route_command(conn, msg):
-    """Send a ground command to the correct turbine."""
+    """Route a command to a turbine."""
     target = msg.get("turbine_id")
     msg["routed_via"]      = SATELLITE_ID
     msg["route_timestamp"] = datetime.utcnow().isoformat() + "Z"
@@ -297,12 +278,12 @@ def _route_command(conn, msg):
     queued  = not is_link_up() or channel_loss()
 
     if target == "ALL":
-        # Broadcast to every connected turbine
+        # Send to every connected turbine.
         with t_lock:
             targets = list(turbine_connections.keys())
         for tid in targets:
             _deliver_or_queue(tid, payload, queued)
-        queued = False  # at least attempted delivery
+        queued = False
     else:
         _deliver_or_queue(target, payload, queued)
 
@@ -321,7 +302,7 @@ def _route_command(conn, msg):
 
 
 def _deliver_or_queue(target, payload, queued):
-    """Deliver a command now, or store it until the turbine is reachable."""
+    """Send a command now or save it for later."""
     if queued:
         command_queue[target].put(payload)
         log.warning(f"Command queued for {target}")
@@ -340,7 +321,7 @@ def _deliver_or_queue(target, payload, queued):
 
 
 def _send_status(conn):
-    """Send basic satellite status back to the ground station."""
+    """Send satellite status to the ground station."""
     with t_lock: tc = len(turbine_connections)
     with g_lock: gc = len(ground_connections)
     stats = get_stats()
@@ -360,11 +341,8 @@ def _send_status(conn):
         pass
 
 
-# ============================================================
-# RELAY & BROADCAST
-# ============================================================
 def relay_loop():
-    """Move telemetry messages from the relay queue to ground stations."""
+    """Forward queued telemetry to ground stations."""
     while True:
         try:
             payload = relay_queue.get(timeout=1)
@@ -374,7 +352,7 @@ def relay_loop():
 
 
 def _broadcast_ground(payload):
-    """Send one message to every connected ground station."""
+    """Send one message to all ground stations."""
     channel_delay()
     with g_lock:
         dead = []
@@ -387,11 +365,8 @@ def _broadcast_ground(payload):
             ground_connections.pop(gid, None)
 
 
-# ============================================================
-# UDP DISCOVERY
-# ============================================================
 def udp_discovery():
-    """Reply to simple UDP discovery requests."""
+    """Reply to UDP discovery requests."""
     with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
         udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
         udp.bind(("0.0.0.0", DISCOVERY_UDP_PORT))
@@ -414,11 +389,8 @@ def udp_discovery():
                 pass
 
 
-# ============================================================
-# STATUS PRINTER
-# ============================================================
 def status_printer():
-    """Print a short satellite status line every few seconds."""
+    """Print satellite status sometimes."""
     while True:
         time.sleep(20)
         with t_lock: tc = len(turbine_connections)
diff --git a/security.py b/security.py
index 1da6a03..071c693 100644
--- a/security.py
+++ b/security.py
@@ -1,25 +1,15 @@
-"""
-CSU33D03 - Main Project 2025-26
-Group 9 - Security Layer
-
-HMAC-SHA256 message signing and replay attack prevention.
-Sits in the project root, imported by all three nodes.
-
-    import sys, os
-    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
-    from security import sign_message, verify_message, strip_security_fields
-"""
+"""Simple message signing used by all nodes."""
 
 import hmac, hashlib, json, time
 
-# Shared secret for this demo. All three programs must use the same value.
+# All programs use the same secret key.
 HMAC_SECRET   = b"csu33d03-group9-arklow-2026"
 HMAC_FIELD    = "sig"
 REPLAY_WINDOW = 30   # seconds - reject messages older than this
 
 
 def sign_message(msg: dict) -> dict:
-    """Add HMAC-SHA256 signature + timestamp. Call before every send."""
+    """Add a timestamp and signature before sending."""
     msg.pop(HMAC_FIELD, None)
     msg["sent_at"] = time.time()
     payload = json.dumps(msg, sort_keys=True, separators=(",", ":"))
@@ -28,10 +18,7 @@ def sign_message(msg: dict) -> dict:
 
 
 def verify_message(msg: dict) -> tuple:
-    """
-    Verify signature and freshness of an incoming message.
-    Returns (True, "") or (False, reason_string).
-    """
+    """Check if a message is signed and recent."""
     received_sig = msg.get(HMAC_FIELD)
     if not received_sig:
         return False, "missing signature"
@@ -57,7 +44,7 @@ def verify_message(msg: dict) -> tuple:
 
 
 def strip_security_fields(msg: dict) -> dict:
-    """Remove sig and sent_at before passing to application logic."""
+    """Remove security fields before normal processing."""
     clean = dict(msg)
     clean.pop(HMAC_FIELD, None)
     clean.pop("sent_at", None)
diff --git a/sensors.py b/sensors.py
index fbdb0cd..a86b622 100644
--- a/sensors.py
+++ b/sensors.py
@@ -1,22 +1,4 @@
-"""
-CSU33D03 - Main Project 2025-26
-Group 9 - Sensor Layer
-
-Provides all sensor readings for the turbine node.
-Two modes:
-  - DATASET MODE (default): replays sensor_data.json row by row
-  - LIVE MODE: uses equipment.py models to compute readings in real time
-
-All readings are exposed through a single SensorSuite object.
-The turbine only needs to call suite.next_reading() each interval.
-
-Usage in turbine.py:
-    import sys, os
-    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
-    from sensors import SensorSuite
-    suite = SensorSuite(turbine_id="TURBINE-01")
-    reading = suite.next_reading(yaw=180.0, pitch=15.0)
-"""
+"""Sensor code for making turbine readings."""
 
 import json, os, math, random, threading, hashlib
 from datetime import datetime
@@ -24,11 +6,11 @@ from equipment import Rotor, Gearbox, Generator, Nacelle, HydraulicSystem
 
 _shared_wind = 12.0
 _shared_wind_lock = threading.Lock()
-# Path to dataset - looks for sensor_data.json in the project root
+# Optional sensor data file in the project root.
 _HERE      = os.path.dirname(os.path.abspath(__file__))
 DATA_FILE  = os.path.join(_HERE, "sensor_data.json")
 
-# Alert thresholds - ground station uses these too
+# Safe ranges for sensor values.
 ALERT_THRESHOLDS = {
     "wind_speed":          {"min": 0,    "max": 25,   "unit": "m/s"},
     "power_output":        {"min": 0,    "max": 2000, "unit": "kW"},
@@ -64,7 +46,7 @@ SENSOR_UNITS = {
 }
 
 def _next_shared_wind():
-    """Move the shared wind value slowly so readings do not jump too much."""
+    """Move wind slowly so values do not jump."""
     global _shared_wind
     with _shared_wind_lock:
         _shared_wind += random.uniform(-0.4, 0.4)
@@ -72,21 +54,15 @@ def _next_shared_wind():
         return round(_shared_wind, 2)
 
 class SensorSuite:
-    """
-    Unified sensor interface for a turbine node.
-    Wraps either the JSON dataset or live equipment models.
-    """
+    """Create sensor readings for one turbine."""
 
     def __init__(self, turbine_id: str = "TURBINE-01", use_dataset: bool = False):
         self.turbine_id  = turbine_id
-        # Priority for selecting mode:
-        # 1. Environment variable SENSORS_MODE ("live" or "dataset")
-        # 2. explicit use_dataset argument
-        # 3. presence of DATA_FILE
+        # Pick dataset mode only when it is requested and the file exists.
         env_mode = os.getenv("SENSORS_MODE")
         if env_mode:
             self.use_dataset = False if env_mode.lower() == "live" else True
-            # if dataset requested but file not present, fall back to live
+            # Use live mode if the dataset file is missing.
             if self.use_dataset and not os.path.exists(DATA_FILE):
                 self.use_dataset = False
         else:
@@ -101,14 +77,14 @@ class SensorSuite:
         }
         self._wind_offset = offset_map.get(tid_num, 0.0)
 
-        # equipment models (used in LIVE mode or to fill derived metrics)
+        # Equipment objects used to create live readings.
         self.rotor      = Rotor()
         self.gearbox    = Gearbox()
         self.generator  = Generator()
         self.nacelle    = Nacelle()
         self.hydraulics = HydraulicSystem()
 
-        # dataset replay
+        # Dataset replay state.
         self._dataset   = []
         self._index     = 0
         self._lock      = threading.Lock()
@@ -121,11 +97,11 @@ class SensorSuite:
             print("[SENSORS] sensor_data.json not found - using live equipment models")
             self.use_dataset = False
 
-        # sequence counter
+        # Message counter.
         self._seq = 0
 
     def _farm_wind(self) -> float:
-        """Create a smooth wind value for the simulated farm."""
+        """Create smooth wind for the farm."""
         t_bucket = int(datetime.utcnow().timestamp() / 2)
         base = 15.0 + 2.0 * math.sin(t_bucket / 12.0) + 0.6 * math.sin(t_bucket / 5.0)
         return round(max(4.0, min(25.0, base)), 2)
@@ -144,7 +120,7 @@ class SensorSuite:
         else:
             raw = self._live_reading(pitch)
 
-        # apply actuator state to affected sensors
+        # Apply yaw and pitch before final values are returned.
         self.rotor.set_pitch(pitch)
         self.nacelle.set_yaw(yaw)
         wind = raw["wind_speed"]
@@ -181,13 +157,13 @@ class SensorSuite:
         }
 
     def _next_dataset_row(self) -> dict:
-        """Return the next row from the JSON dataset."""
+        """Return the next dataset row."""
         row = self._dataset[self._index]
         self._index = (self._index + 1) % len(self._dataset)
         return row
 
     def _live_reading(self, pitch: float) -> dict:
-        """Generate a reading from the equipment models."""
+        """Create one live sensor reading."""
         base_wind = self._farm_wind()
         wind = round(base_wind + self._wind_offset + random.uniform(-0.15, 0.15), 2)
         wind = max(4.0, min(25.0, wind))
@@ -209,7 +185,7 @@ class SensorSuite:
         }
 
     def get_single(self, sensor_name: str, yaw: float = 180.0, pitch: float = 15.0):
-        """Read one sensor by name - used by the per-sensor TCP servers."""
+        """Read one sensor by name."""
         reading = self.next_reading(yaw, pitch)
         val = reading["sensors"].get(sensor_name) or reading["derived"].get(sensor_name)
         return {
diff --git a/turbine/turbine.py b/turbine/turbine.py
index 8fea1c0..db875aa 100644
--- a/turbine/turbine.py
+++ b/turbine/turbine.py
@@ -1,18 +1,4 @@
-"""
-CSU33D03 - Main Project 2025-26
-TURBINE NODE  -  Dynamic collaborative version (leader-based)
-
-This script simulates an offshore wind turbine. One turbine acts as the LEADER
-(elected by best link quality) and sends aggregated telemetry for the farm to
-the satellite. Other turbines act as followers but still expose their own sensors
-and can receive commands individually.
-
-Main features:
-- Sends telemetry to the satellite.
-- Receives yaw, pitch, stop, and resume commands.
-- Elects one turbine as the leader using simulated link quality.
-- Sends a farm alert when wind becomes unsafe.
-"""
+"""Turbine program that sends sensor data and receives commands."""
 
 import socket, threading, time, random, json, logging, sys, os
 from datetime import datetime
@@ -21,22 +7,16 @@ sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
 from security import sign_message, verify_message, strip_security_fields
 from sensors import SensorSuite
 
-# =========================================================
-# NETWORK SETTINGS
-# =========================================================
-# Use localhost for running everything on one computer.
-# For a real network test, set SATELLITE_HOST to the satellite computer IP.
+# Use localhost when everything runs on one computer.
+# Change SATELLITE_HOST when the satellite is on another computer.
 SATELLITE_HOST = os.getenv("SATELLITE_HOST", "127.0.0.1")
 SATELLITE_PORT = int(os.getenv("SATELLITE_TURBINE_PORT", "9000"))
 
 RECONNECT_DELAY  = 5
 SENSOR_INTERVAL  = 2
-WIND_ESTOP_LIMIT = 25.0   # m/s — leader broadcasts FARM_ALERT above this
+WIND_ESTOP_LIMIT = 25.0   # stop farm above this wind speed
 
-# =========================================================
-# TURBINE IDENTITY
 # Argv: python turbine.py <TURBINE-ID> [BASE_PORT] [T1,T2,T3,...]
-# =========================================================
 TURBINE_ID = sys.argv[1] if len(sys.argv) > 1 else "TURBINE-01"
 
 BASE_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else (
@@ -44,10 +24,7 @@ BASE_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else (
     if TURBINE_ID.split("-")[-1].isdigit() else 5001
 )
 
-# Allow passing the full farm list as a third argument so any number of turbines
-# can participate without editing this file.
-# e.g.  python turbine.py TURBINE-01 5010 TURBINE-01,TURBINE-02,TURBINE-03,TURBINE-04
-# Can also be set via env var: FARM_TURBINES=TURBINE-01,TURBINE-02,...
+# Farm list can come from the command line or FARM_TURBINES.
 if len(sys.argv) > 3:
     ALL_TURBINES = sys.argv[3].split(",")
 elif os.getenv("FARM_TURBINES"):
@@ -55,11 +32,7 @@ elif os.getenv("FARM_TURBINES"):
 else:
     ALL_TURBINES = ["TURBINE-01", "TURBINE-02", "TURBINE-03"]
 
-# =========================================================
-# DYNAMIC LEADER ELECTION  (best link quality wins)
-# =========================================================
-# Each turbine measures its own link quality independently in a real system.
-# Here we simulate it with a fixed table that can be extended as ALL_TURBINES grows.
+# The turbine with the best fake link quality becomes leader.
 _BASE_LINK_QUALITY = {
     "TURBINE-01": {"avg_delay_ms": 140, "loss_pct": 2.8},
     "TURBINE-02": {"avg_delay_ms": 120, "loss_pct": 1.8},
@@ -69,7 +42,7 @@ _BASE_LINK_QUALITY = {
 }
 
 def _default_link(tid):
-    """Generate plausible link stats for turbines not in the table."""
+    """Make simple link values for a new turbine."""
     seed = sum(ord(c) for c in tid)
     rng  = random.Random(seed)
     return {"avg_delay_ms": rng.randint(100, 180), "loss_pct": round(rng.uniform(1.5, 4.5), 1)}
@@ -80,11 +53,11 @@ SIMULATED_LINK_QUALITY = {
 }
 
 def compute_leader_score(delay_ms, loss_pct):
-    """Higher score means a better turbine to act as leader."""
+    """Give a higher score to a better link."""
     return round(1000 - (delay_ms * 2) - (loss_pct * 100), 2)
 
 def elect_leader():
-    """Pick the turbine with the best simulated link quality."""
+    """Pick the best turbine as leader."""
     best_tid   = None
     best_score = -999999
     for tid in ALL_TURBINES:
@@ -99,22 +72,15 @@ LEADER_ID, LEADER_SCORE = elect_leader()
 IS_LEADER = (TURBINE_ID == LEADER_ID)
 FOLLOWERS = [tid for tid in ALL_TURBINES if tid != LEADER_ID]
 
-# =========================================================
-# SENSOR SUITES
-# One SensorSuite for this turbine; one per follower so each
-# gets independent, physically-consistent readings.
-# =========================================================
+# One sensor suite is used for each turbine.
 _local_suite = SensorSuite(turbine_id=TURBINE_ID)
 _follower_suites = {tid: SensorSuite(turbine_id=tid) for tid in FOLLOWERS}
-# Tracks last-known actuator/status state for each follower (updated on ACK or command)
+# Store the last state for follower turbines.
 _follower_states = {
     tid: {"yaw_angle": 180.0, "blade_pitch": 15.0, "emergency_stop": False, "online": True}
     for tid in FOLLOWERS
 }
 
-# =========================================================
-# SENSOR PORTS
-# =========================================================
 SENSOR_NAMES = ["wind_speed", "power_output", "rotor_rpm", "temperature"]
 SENSOR_PORTS = {name: BASE_PORT + i + 1 for i, name in enumerate(SENSOR_NAMES)}
 SENSOR_UNITS = {
@@ -124,9 +90,6 @@ SENSOR_UNITS = {
     "temperature":  "°C"
 }
 
-# =========================================================
-# LOGGING
-# =========================================================
 logging.basicConfig(
     level=logging.INFO,
     format=f"%(asctime)s [{TURBINE_ID}] %(levelname)s - %(message)s",
@@ -134,9 +97,6 @@ logging.basicConfig(
 )
 log = logging.getLogger(TURBINE_ID)
 
-# =========================================================
-# TURBINE STATE
-# =========================================================
 state = {
     "yaw_angle":      180.0,
     "blade_pitch":    15.0,
@@ -145,15 +105,12 @@ state = {
 }
 state_lock = threading.Lock()
 
-# Track whether we already sent a farm-level wind alert so we don't spam
+# Remember if a wind alert was already sent.
 _wind_alert_sent = False
 _wind_alert_lock = threading.Lock()
 
-# =========================================================
-# SENSOR READERS
-# =========================================================
 def read_wind_speed():
-    """Return a simple wind speed value for the small sensor server."""
+    """Return a wind speed value."""
     return round(12.0 + random.gauss(0, 2.5), 2)
 
 def read_power_output():
@@ -162,7 +119,7 @@ def read_power_output():
     return round(min(2000.0, max(0.0, 0.5 * 1.225 * 3.14159 * (40**2) * (ws**3) / 1000)), 1)
 
 def read_rotor_rpm():
-    """Return rotor speed, or zero when emergency stop is active."""
+    """Return rotor speed."""
     with state_lock:
         pitch = state["blade_pitch"]
         estop = state["emergency_stop"]
@@ -181,11 +138,9 @@ READERS = {
     "temperature":  read_temperature,
 }
 
-# =========================================================
 # SENSOR TCP SERVERS  (individual sensor ports)
-# =========================================================
 def sensor_server(name, port):
-    """Start a tiny TCP server for one sensor value."""
+    """Start a TCP server for one sensor."""
     with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
         srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
         srv.bind(("0.0.0.0", port))
@@ -216,17 +171,14 @@ def _handle_sensor(conn, name):
         except Exception:
             pass
 
-# =========================================================
-# TELEMETRY BUILDERS
-# =========================================================
 def build_local_telemetry():
-    """Build a single TELEMETRY message for this turbine, including derived metrics."""
+    """Build telemetry for this turbine."""
     with state_lock:
         s = dict(state)
     reading = _local_suite.next_reading(yaw=s["yaw_angle"], pitch=s["blade_pitch"])
     sensors = reading["sensors"]
     derived = reading["derived"]
-    # Emergency stop overrides RPM
+    # Stop means the rotor is not moving.
     if s["emergency_stop"]:
         sensors["rotor_rpm"] = 0.0
     return {
@@ -250,11 +202,7 @@ def build_local_telemetry():
     }
 
 def build_follower_telemetry(tid):
-    """
-    Leader relays each follower's data using its own SensorSuite so readings
-    are physically consistent and reflect any commands sent to that follower.
-    Follower actuator/status state is tracked in _follower_states.
-    """
+    """Build telemetry for a follower turbine."""
     fstate = _follower_states.get(tid, {"yaw_angle": 180.0, "blade_pitch": 15.0,
                                         "emergency_stop": False, "online": True})
     suite   = _follower_suites[tid]
@@ -282,11 +230,8 @@ def build_follower_telemetry(tid):
         },
     }
 
-# =========================================================
-# COMMAND HANDLER
-# =========================================================
 def _apply_state_change(action, params, s):
-    """Apply a single action to a state dict (in-place). Returns (success, message)."""
+    """Apply one command to a turbine state."""
     if action == "SET_YAW":
         angle = max(0.0, min(360.0, float(params.get("angle", s["yaw_angle"]))))
         s["yaw_angle"] = angle
@@ -317,7 +262,7 @@ def apply_command(cmd):
     success = True
     message = "OK"
 
-    # Apply to this turbine's own state
+    # Apply the command to this turbine.
     with state_lock:
         try:
             success, message = _apply_state_change(action, params, state)
@@ -329,9 +274,7 @@ def apply_command(cmd):
             success = False
             message = f"Command error: {e}"
 
-    # If this turbine is the leader and the command was broadcast to ALL,
-    # also mirror the state change into all follower shadow states so
-    # build_follower_telemetry() reflects the correct pitch/estop.
+    # If the command is for all turbines, update follower states too.
     if IS_LEADER and target == "ALL":
         for tid, fstate in _follower_states.items():
             try:
@@ -348,11 +291,8 @@ def apply_command(cmd):
         "timestamp":  datetime.utcnow().isoformat() + "Z",
     }
 
-# =========================================================
-# SATELLITE LINK
-# =========================================================
 def satellite_link():
-    """Connect to the satellite and exchange telemetry and commands."""
+    """Connect to the satellite."""
     global _wind_alert_sent
 
     while True:
@@ -384,21 +324,21 @@ def satellite_link():
                 while True:
                     now = time.time()
 
-                    # ---- Periodic telemetry transmission ----
+                    # Send telemetry every few seconds.
                     if now - last_tx >= SENSOR_INTERVAL:
                         last_tx = now
 
-                        # Always send our own telemetry
+                        # Send this turbine's data.
                         local = build_local_telemetry()
                         sock.sendall((json.dumps(sign_message(local)) + "\n").encode())
 
-                        # Leader additionally relays all follower telemetry
+                        # The leader also sends follower data.
                         if IS_LEADER:
                             for tid in FOLLOWERS:
                                 follower_msg = build_follower_telemetry(tid)
                                 sock.sendall((json.dumps(sign_message(follower_msg)) + "\n").encode())
 
-                            # Check for farm-wide high wind and send a FARM_ALERT
+                            # Send an alert if wind is too high.
                             wind = local["sensors"]["wind_speed"]
                             with _wind_alert_lock:
                                 if wind > WIND_ESTOP_LIMIT and not _wind_alert_sent:
@@ -418,10 +358,10 @@ def satellite_link():
                                     )
                                     _wind_alert_sent = True
                                 elif wind <= WIND_ESTOP_LIMIT * 0.85:
-                                    # Wind has dropped back to safe range — allow future alerts
+                                    # Allow a new alert after wind becomes safe.
                                     _wind_alert_sent = False
 
-                    # ---- Receive commands / ACKs ----
+                    # Read commands from the satellite.
                     try:
                         chunk = sock.recv(4096).decode()
                         if not chunk:
