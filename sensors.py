"""Sensor code for making turbine readings."""

import json, os, math, random, threading, hashlib
from datetime import datetime
from equipment import Rotor, Gearbox, Generator, Nacelle, HydraulicSystem

_shared_wind = 12.0
_shared_wind_lock = threading.Lock()
# Optional sensor data file in the project root.
_HERE      = os.path.dirname(os.path.abspath(__file__))
DATA_FILE  = os.path.join(_HERE, "sensor_data.json")

# Safe ranges for sensor values.
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
    "temperature":         65,
    "gearbox_temp":        88,
    "vibration":           7.5,
    "wind_speed":          25,
}

SENSOR_UNITS = {
    "wind_speed":          "m/s",
    "power_output":        "kW",
    "rotor_rpm":           "RPM",
    "temperature":         "C",
    "gearbox_temp":        "C",
    "vibration":           "mm/s",
    "hydraulic_pressure":  "bar",
    "nacelle_humidity":    "%",
    "capacity_factor":     "",
    "tip_speed_ratio":     "",
    "power_coefficient":   "",
    "mech_efficiency":     "",
    "aep_projection_mwh":  "MWh",
}

def _next_shared_wind():
    """Move wind slowly so values do not jump."""
    global _shared_wind
    with _shared_wind_lock:
        _shared_wind += random.uniform(-0.4, 0.4)
        _shared_wind = max(4.0, min(25.0, _shared_wind))
        return round(_shared_wind, 2)

class SensorSuite:
    """Create sensor readings for one turbine."""

    def __init__(self, turbine_id: str = "TURBINE-01", use_dataset: bool = False):
        self.turbine_id  = turbine_id
        # Pick dataset mode only when it is requested and the file exists.
        env_mode = os.getenv("SENSORS_MODE")
        if env_mode:
            self.use_dataset = False if env_mode.lower() == "live" else True
            # Use live mode if the dataset file is missing.
            if self.use_dataset and not os.path.exists(DATA_FILE):
                self.use_dataset = False
        else:
            self.use_dataset = use_dataset and os.path.exists(DATA_FILE)

        tail = turbine_id.split("-")[-1]
        tid_num = int(tail) if tail.isdigit() else 1
        offset_map = {
            1: -0.25,
            2:  0.10,
            3:  0.25,
        }
        self._wind_offset = offset_map.get(tid_num, 0.0)

        # Equipment objects used to create live readings.
        self.rotor      = Rotor()
        self.gearbox    = Gearbox()
        self.generator  = Generator()
        self.nacelle    = Nacelle()
        self.hydraulics = HydraulicSystem()

        # Dataset replay state.
        self._dataset   = []
        self._index     = 0
        self._lock      = threading.Lock()

        if self.use_dataset:
            with open(DATA_FILE) as f:
                self._dataset = json.load(f)
            print(f"[SENSORS] Loaded {len(self._dataset)} readings from sensor_data.json")
        else:
            print("[SENSORS] sensor_data.json not found - using live equipment models")
            self.use_dataset = False

        # Message counter.
        self._seq = 0

    def _farm_wind(self) -> float:
        """Create smooth wind for the farm."""
        t_bucket = int(datetime.utcnow().timestamp() / 2)
        base = 15.0 + 2.0 * math.sin(t_bucket / 12.0) + 0.6 * math.sin(t_bucket / 5.0)
        return round(max(4.0, min(25.0, base)), 2)

    def next_reading(self, yaw: float = 180.0, pitch: float = 15.0) -> dict:
        """
        Advance to the next sensor reading and return a full telemetry dict.
        Call this once per telemetry interval.
        """
        with self._lock:
            self._seq += 1
            seq = self._seq

        if self.use_dataset:
            raw = self._next_dataset_row()
        else:
            raw = self._live_reading(pitch)

        # Apply yaw and pitch before final values are returned.
        self.rotor.set_pitch(pitch)
        self.nacelle.set_yaw(yaw)
        wind = raw["wind_speed"]

        sensors = {
            "wind_speed":          raw["wind_speed"],
            "power_output":        raw["power_output"],
            "rotor_rpm":           max(0.0, round(raw["rotor_rpm"] * (1.0 - pitch / 120.0), 2)),
            "temperature":         raw["temperature"],
            "gearbox_temp":        raw["gearbox_temp"],
            "vibration":           raw["vibration"],
            "hydraulic_pressure":  raw["hydraulic_pressure"],
            "nacelle_humidity":    raw["nacelle_humidity"],
        }

        derived = {
            "capacity_factor":    round(sensors["power_output"] / 2000.0, 3),
            "tip_speed_ratio":    self.rotor.tip_speed_ratio(wind),
            "power_coefficient":  self.rotor.power_coefficient(wind),
            "mech_efficiency":    round(max(0.50, 0.97 - (sensors["vibration"] / 8.0) * 0.18), 3),
            "aep_projection_mwh": round(sensors["power_output"] * 8760 / 1000, 1),
        }

        checksum = hashlib.md5(
            json.dumps(sensors, sort_keys=True).encode()
        ).hexdigest()[:8]

        return {
            "sensors":   sensors,
            "derived":   derived,
            "seq":       seq,
            "checksum":  checksum,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _next_dataset_row(self) -> dict:
        """Return the next dataset row."""
        row = self._dataset[self._index]
        self._index = (self._index + 1) % len(self._dataset)
        return row

    def _live_reading(self, pitch: float) -> dict:
        """Create one live sensor reading."""
        base_wind = self._farm_wind()
        wind = round(base_wind + self._wind_offset + random.uniform(-0.15, 0.15), 2)
        wind = max(4.0, min(25.0, wind))

        self.rotor.set_pitch(pitch)
        rpm  = self.rotor.rpm(wind)
        self.gearbox.update(rpm)
        self.generator.update(rpm, wind)
        self.hydraulics.update(pitch_changing=False)
        return {
            "wind_speed":         wind,
            "power_output":       self.generator.power_output_kw(wind),
            "rotor_rpm":          rpm,
            "temperature":        self.generator.temperature(),
            "gearbox_temp":       self.gearbox.temperature(),
            "vibration":          self.nacelle.vibration(self.rotor.vibration()),
            "hydraulic_pressure": self.hydraulics.pressure(),
            "nacelle_humidity":   self.nacelle.humidity(),
        }

    def get_single(self, sensor_name: str, yaw: float = 180.0, pitch: float = 15.0):
        """Read one sensor by name."""
        reading = self.next_reading(yaw, pitch)
        val = reading["sensors"].get(sensor_name) or reading["derived"].get(sensor_name)
        return {
            "sensor":     sensor_name,
            "value":      val,
            "unit":       SENSOR_UNITS.get(sensor_name, ""),
            "turbine_id": self.turbine_id,
            "timestamp":  reading["timestamp"],
        }
