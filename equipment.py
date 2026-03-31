"""
CSU33D03 - Main Project 2025-26
Group 9 - Physical Equipment Models

Models the physical components of a wind turbine.
Each class tracks its own state and health, and produces realistic
sensor readings based on operating conditions.

Components modelled:
  - Rotor       blades, pitch control, tip speed ratio
  - Gearbox     temperature, oil pressure, wear factor
  - Generator   temperature, output power, efficiency
  - Nacelle     yaw system, humidity, vibration
  - HydraulicSystem  blade pitch actuator pressure
  - Tower       base vibration, structural health

Imported by sensors.py to produce physically-consistent readings.

Usage:
    from equipment import Rotor, Gearbox, Generator, Nacelle, HydraulicSystem
    rotor     = Rotor()
    gearbox   = Gearbox()
    generator = Generator()
    nacelle   = Nacelle()
    hydraulics = HydraulicSystem()
"""

import math, random, time


class Rotor:
    """
    Models the rotor assembly: three blades, hub, pitch actuators.
    Blade pitch (0-90 deg) controls how much wind energy is captured.
    0 deg = full power, 90 deg = feathered (stopped).
    """
    def __init__(self):
        self.blade_pitch   = 15.0    # degrees
        self.diameter_m    = 80.0    # rotor diameter in metres
        self.radius_m      = 40.0
        self.rated_rpm     = 15.0
        self._wear         = 0.0     # 0.0 = new, 1.0 = end of life

    def set_pitch(self, degrees: float):
        self.blade_pitch = max(0.0, min(90.0, degrees))

    def rpm(self, wind_speed: float) -> float:
        """Calculate rotor RPM from wind speed and blade pitch."""
        if wind_speed < 3.0 or self.blade_pitch > 85.0:
            return 0.0
        pitch_factor = 1.0 - (self.blade_pitch / 90.0)
        base_rpm     = min(self.rated_rpm, wind_speed * 0.72 * pitch_factor)
        noise        = random.gauss(0, 0.15)
        return max(0.0, round(base_rpm + noise, 2))

    def tip_speed_ratio(self, wind_speed: float) -> float:
        """TSR = blade tip speed / wind speed. Optimal ~7 for most turbines."""
        rpm = self.rpm(wind_speed)
        tip_speed = (rpm * 2 * math.pi / 60) * self.radius_m
        return round(tip_speed / max(wind_speed, 0.1), 2)

    def power_coefficient(self, wind_speed: float) -> float:
        """Aerodynamic efficiency. Betz limit = 0.593."""
        tsr = self.tip_speed_ratio(wind_speed)
        cp  = 0.22 * (0.8 * tsr - 0.035 * tsr**3 + 0.003)
        return round(max(0.0, min(0.593, cp)), 4)

    def vibration(self) -> float:
        """Rotor vibration in mm/s. Increases with wear and imbalance."""
        base = 1.2 + self._wear * 3.0
        return round(max(0.1, base + random.gauss(0, 0.1)), 2)

    def age(self, hours: float = 1.0):
        """Simulate wear accumulation over operating hours."""
        self._wear = min(1.0, self._wear + hours * 0.000001)


class Gearbox:
    """
    Models the gearbox: converts low-speed rotor rotation to
    high-speed generator input. Main heat source in the drivetrain.
    """
    def __init__(self):
        self.gear_ratio  = 100.0    # rotor RPM * gear_ratio = generator RPM
        self._temp_c     = 45.0     # internal temperature
        self._oil_degr   = 0.0      # oil degradation 0=fresh, 1=needs change
        self._wear       = 0.0

    def update(self, rotor_rpm: float, ambient_temp: float = 15.0):
        """Update gearbox temperature based on load."""
        load_heat  = (rotor_rpm / 15.0) * 35.0
        target     = ambient_temp + load_heat + self._wear * 10.0
        # temperature lags behind load (thermal mass)
        self._temp_c += (target - self._temp_c) * 0.04
        self._temp_c += random.gauss(0, 0.3)

    def temperature(self) -> float:
        return round(self._temp_c, 1)

    def oil_pressure_bar(self) -> float:
        """Oil pressure drops as oil degrades."""
        base = 4.5 - self._oil_degr * 1.5
        return round(max(1.0, base + random.gauss(0, 0.05)), 2)

    def efficiency(self) -> float:
        """Mechanical efficiency, degrades with wear."""
        return round(max(0.88, 0.98 - self._wear * 0.08), 3)

    def age(self, hours: float = 1.0):
        self._wear    = min(1.0, self._wear    + hours * 0.0000015)
        self._oil_degr = min(1.0, self._oil_degr + hours * 0.000005)


class Generator:
    """
    Models the electrical generator. Converts mechanical power to electricity.
    Temperature is the primary health indicator.
    """
    RATED_KW = 2000.0

    def __init__(self):
        self._temp_c      = 35.0
        self._efficiency  = 0.96
        self._wear        = 0.0

    def update(self, rotor_rpm: float, wind_speed: float, ambient_temp: float = 15.0):
        """Update generator temperature and output based on wind conditions."""
        power   = self.power_output_kw(wind_speed)
        load    = power / self.RATED_KW
        target  = ambient_temp + 15.0 + load * 35.0 + self._wear * 5.0
        self._temp_c += (target - self._temp_c) * 0.05
        self._temp_c += random.gauss(0, 0.8)

    def temperature(self) -> float:
        return round(self._temp_c, 1)

    def power_output_kw(self, wind_speed: float) -> float:
        """Power from wind using simplified kinetic energy formula."""
        rho  = 1.225   # air density kg/m3
        area = math.pi * (40 ** 2)
        raw  = 0.5 * rho * area * (wind_speed ** 3) / 1000.0
        return round(min(self.RATED_KW, max(0.0, raw * self._efficiency)), 1)

    def capacity_factor(self, wind_speed: float) -> float:
        return round(self.power_output_kw(wind_speed) / self.RATED_KW, 3)

    def aep_projection_mwh(self, wind_speed: float) -> float:
        """Projected annual energy at current output."""
        return round(self.power_output_kw(wind_speed) * 8760 / 1000, 1)

    def age(self, hours: float = 1.0):
        self._wear       = min(1.0, self._wear + hours * 0.000001)
        self._efficiency = max(0.88, 0.96 - self._wear * 0.06)


class Nacelle:
    """
    Models the nacelle housing: contains the gearbox, generator, and yaw system.
    Tracks yaw angle, internal humidity and structural vibration.
    """
    def __init__(self):
        self.yaw_angle  = 180.0   # degrees, 0=North, 180=South
        self._humidity  = 55.0    # % relative humidity inside nacelle
        self._vib       = 1.2     # structural vibration mm/s

    def set_yaw(self, degrees: float):
        self.yaw_angle = max(0.0, min(360.0, degrees))

    def humidity(self) -> float:
        """Nacelle humidity drifts slowly."""
        self._humidity += random.gauss(0, 0.3)
        self._humidity  = max(30.0, min(90.0, self._humidity))
        return round(self._humidity, 1)

    def vibration(self, rotor_vib: float) -> float:
        """Nacelle vibration tracks rotor vibration with damping."""
        self._vib += (rotor_vib - self._vib) * 0.1
        self._vib += random.gauss(0, 0.05)
        return round(max(0.1, self._vib), 2)


class HydraulicSystem:
    """
    Models the hydraulic system used to actuate blade pitch changes.
    Pressure must be maintained for pitch control to work.
    Low pressure = pitch control failure risk.
    """
    NOMINAL_BAR = 180.0
    MAX_BAR     = 280.0
    MIN_BAR     = 120.0

    def __init__(self):
        self._pressure = self.NOMINAL_BAR
        self._leak_rate = 0.0    # simulates slow leak developing over time

    def update(self, pitch_changing: bool = False):
        """Pressure drops when pitch is being adjusted, recovers via pump."""
        if pitch_changing:
            self._pressure -= random.uniform(2.0, 5.0)
        else:
            # pump restores pressure
            self._pressure += (self.NOMINAL_BAR - self._pressure) * 0.08
        self._pressure -= self._leak_rate
        self._pressure += random.gauss(0, 1.2)
        self._pressure  = max(self.MIN_BAR, min(self.MAX_BAR, self._pressure))

    def pressure(self) -> float:
        return round(self._pressure, 1)

    def health(self) -> str:
        if self._pressure < 130.0:  return "CRITICAL"
        if self._pressure < 150.0:  return "WARNING"
        return "OK"

    def develop_leak(self):
        """Simulate a slow leak developing - useful for demo fault injection."""
        self._leak_rate = 0.5