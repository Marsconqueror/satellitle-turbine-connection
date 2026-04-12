"""Simple wind turbine equipment models."""

import math, random, time


class Rotor:
    """Model the turbine rotor."""
    def __init__(self):
        self.blade_pitch   = 15.0    # degrees
        self.diameter_m    = 80.0    # rotor diameter in metres
        self.radius_m      = 40.0
        self.rated_rpm     = 15.0
        self._wear         = 0.0     # 0.0 = new, 1.0 = end of life

    def set_pitch(self, degrees: float):
        self.blade_pitch = max(0.0, min(90.0, degrees))

    def rpm(self, wind_speed: float) -> float:
        """Calculate rotor speed from wind and pitch."""
        if wind_speed < 3.0 or self.blade_pitch > 85.0:
            return 0.0
        pitch_factor = 1.0 - (self.blade_pitch / 90.0)
        base_rpm     = min(self.rated_rpm, wind_speed * 0.72 * pitch_factor)
        noise        = random.gauss(0, 0.15)
        return max(0.0, round(base_rpm + noise, 2))

    def tip_speed_ratio(self, wind_speed: float) -> float:
        """Calculate blade tip speed compared to wind speed."""
        rpm = self.rpm(wind_speed)
        tip_speed = (rpm * 2 * math.pi / 60) * self.radius_m
        return round(tip_speed / max(wind_speed, 0.1), 2)

    def power_coefficient(self, wind_speed: float) -> float:
        """Calculate simple rotor efficiency."""
        tsr = self.tip_speed_ratio(wind_speed)
        cp  = 0.22 * (0.8 * tsr - 0.035 * tsr**3 + 0.003)
        return round(max(0.0, min(0.593, cp)), 4)

    def vibration(self) -> float:
        """Return rotor vibration."""
        base = 1.2 + self._wear * 3.0
        return round(max(0.1, base + random.gauss(0, 0.1)), 2)

    def age(self, hours: float = 1.0):
        """Add a small amount of rotor wear."""
        self._wear = min(1.0, self._wear + hours * 0.000001)


class Gearbox:
    """Model the turbine gearbox."""
    def __init__(self):
        self.gear_ratio  = 100.0    # rotor RPM * gear_ratio = generator RPM
        self._temp_c     = 45.0     # internal temperature
        self._oil_degr   = 0.0      # oil degradation 0=fresh, 1=needs change
        self._wear       = 0.0

    def update(self, rotor_rpm: float, ambient_temp: float = 15.0):
        """Update gearbox temperature."""
        load_heat  = (rotor_rpm / 15.0) * 35.0
        target     = ambient_temp + load_heat + self._wear * 10.0
        # Temperature changes slowly.
        self._temp_c += (target - self._temp_c) * 0.04
        self._temp_c += random.gauss(0, 0.3)

    def temperature(self) -> float:
        """Return the current gearbox temperature."""
        return round(self._temp_c, 1)

    def oil_pressure_bar(self) -> float:
        """Return oil pressure."""
        base = 4.5 - self._oil_degr * 1.5
        return round(max(1.0, base + random.gauss(0, 0.05)), 2)

    def efficiency(self) -> float:
        """Return gearbox efficiency."""
        return round(max(0.88, 0.98 - self._wear * 0.08), 3)

    def age(self, hours: float = 1.0):
        """Add a small amount of gearbox wear."""
        self._wear    = min(1.0, self._wear    + hours * 0.0000015)
        self._oil_degr = min(1.0, self._oil_degr + hours * 0.000005)


class Generator:
    """Model the electrical generator."""
    RATED_KW = 2000.0

    def __init__(self):
        self._temp_c      = 35.0
        self._efficiency  = 0.96
        self._wear        = 0.0

    def update(self, rotor_rpm: float, wind_speed: float, ambient_temp: float = 15.0):
        """Update generator temperature."""
        power   = self.power_output_kw(wind_speed)
        load    = power / self.RATED_KW
        target  = ambient_temp + 15.0 + load * 35.0 + self._wear * 5.0
        self._temp_c += (target - self._temp_c) * 0.05
        self._temp_c += random.gauss(0, 0.8)

    def temperature(self) -> float:
        """Return the current generator temperature."""
        return round(self._temp_c, 1)

    def power_output_kw(self, wind_speed: float) -> float:
        """Estimate power output from wind speed."""
        rho  = 1.225   # air density kg/m3
        area = math.pi * (40 ** 2)
        raw  = 0.5 * rho * area * (wind_speed ** 3) / 1000.0
        return round(min(self.RATED_KW, max(0.0, raw * self._efficiency)), 1)

    def capacity_factor(self, wind_speed: float) -> float:
        """Return output as a fraction of rated power."""
        return round(self.power_output_kw(wind_speed) / self.RATED_KW, 3)

    def aep_projection_mwh(self, wind_speed: float) -> float:
        """Estimate yearly energy from current output."""
        return round(self.power_output_kw(wind_speed) * 8760 / 1000, 1)

    def age(self, hours: float = 1.0):
        """Add a small amount of generator wear."""
        self._wear       = min(1.0, self._wear + hours * 0.000001)
        self._efficiency = max(0.88, 0.96 - self._wear * 0.06)


class Nacelle:
    """Model the nacelle."""
    def __init__(self):
        self.yaw_angle  = 180.0   # degrees, 0=North, 180=South
        self._humidity  = 55.0    # % relative humidity inside nacelle
        self._vib       = 1.2     # structural vibration mm/s

    def set_yaw(self, degrees: float):
        """Set nacelle direction between 0 and 360 degrees."""
        self.yaw_angle = max(0.0, min(360.0, degrees))

    def humidity(self) -> float:
        """Return nacelle humidity."""
        self._humidity += random.gauss(0, 0.3)
        self._humidity  = max(30.0, min(90.0, self._humidity))
        return round(self._humidity, 1)

    def vibration(self, rotor_vib: float) -> float:
        """Return nacelle vibration."""
        self._vib += (rotor_vib - self._vib) * 0.1
        self._vib += random.gauss(0, 0.05)
        return round(max(0.1, self._vib), 2)


class HydraulicSystem:
    """Model the blade pitch hydraulic system."""
    NOMINAL_BAR = 180.0
    MAX_BAR     = 280.0
    MIN_BAR     = 120.0

    def __init__(self):
        self._pressure = self.NOMINAL_BAR
        self._leak_rate = 0.0    # simulates slow leak developing over time

    def update(self, pitch_changing: bool = False):
        """Update hydraulic pressure."""
        if pitch_changing:
            self._pressure -= random.uniform(2.0, 5.0)
        else:
            # Pump brings pressure back up.
            self._pressure += (self.NOMINAL_BAR - self._pressure) * 0.08
        self._pressure -= self._leak_rate
        self._pressure += random.gauss(0, 1.2)
        self._pressure  = max(self.MIN_BAR, min(self.MAX_BAR, self._pressure))

    def pressure(self) -> float:
        """Return current hydraulic pressure."""
        return round(self._pressure, 1)

    def health(self) -> str:
        """Return a simple health label based on pressure."""
        if self._pressure < 130.0:  return "CRITICAL"
        if self._pressure < 150.0:  return "WARNING"
        return "OK"

    def develop_leak(self):
        """Start a small simulated leak."""
        self._leak_rate = 0.5
