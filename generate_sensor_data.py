import json
import random
import os

def clamp(value, low, high):
    return max(low, min(value, high))

def power_curve(wind_speed, rated_power=2000.0):
    if wind_speed < 3:
        return 0.0
    elif wind_speed < 12:
        return rated_power * ((wind_speed - 3) / 9) ** 3
    elif wind_speed < 25:
        return rated_power * random.uniform(0.92, 1.00)
    else:
        return 0.0

def init_reading():
    wind_speed = round(random.uniform(7, 12), 2)
    power_output = round(power_curve(wind_speed), 1)
    rotor_rpm = round(0.72 * wind_speed + random.uniform(-0.4, 0.4), 2)

    return {
        "wind_speed": wind_speed,
        "power_output": power_output,
        "rotor_rpm": rotor_rpm,
        "temperature": round(random.uniform(42, 48), 1),
        "gearbox_temp": round(random.uniform(44, 47), 1),
        "vibration": round(random.uniform(0.8, 1.6), 2),
        "hydraulic_pressure": round(random.uniform(168, 178), 1),
        "nacelle_humidity": round(random.uniform(53, 57), 1)
    }

def next_reading(prev, fault_mode=None):
    wind_speed = prev["wind_speed"] + random.uniform(-0.6, 0.6)
    wind_speed = clamp(wind_speed, 4.0, 25.0)

    target_rpm = 0.72 * wind_speed
    rotor_rpm = prev["rotor_rpm"] + 0.35 * (target_rpm - prev["rotor_rpm"]) + random.uniform(-0.25, 0.25)
    rotor_rpm = clamp(rotor_rpm, 3.0, 18.5)

    target_power = power_curve(wind_speed)
    power_output = prev["power_output"] + 0.4 * (target_power - prev["power_output"]) + random.uniform(-40, 40)
    power_output = clamp(power_output, 0.0, 2000.0)

    target_temp = 38 + (power_output / 2000.0) * 13
    temperature = prev["temperature"] + 0.18 * (target_temp - prev["temperature"]) + random.uniform(-0.7, 0.7)
    temperature = clamp(temperature, 35.0, 70.0)

    target_gearbox = 42 + (power_output / 2000.0) * 10 + (rotor_rpm / 18.5) * 2
    gearbox_temp = prev["gearbox_temp"] + 0.12 * (target_gearbox - prev["gearbox_temp"]) + random.uniform(-0.4, 0.4)
    gearbox_temp = clamp(gearbox_temp, 40.0, 90.0)

    target_vibration = 0.35 + 0.08 * rotor_rpm + 0.6 * (power_output / 2000.0)
    vibration = prev["vibration"] + 0.25 * (target_vibration - prev["vibration"]) + random.uniform(-0.12, 0.12)
    vibration = clamp(vibration, 0.3, 8.5)

    target_pressure = 165 - (power_output / 2000.0) * 18 + random.uniform(-1.5, 1.5)
    hydraulic_pressure = prev["hydraulic_pressure"] + 0.15 * (target_pressure - prev["hydraulic_pressure"]) + random.uniform(-1.0, 1.0)
    hydraulic_pressure = clamp(hydraulic_pressure, 138.0, 182.0)

    nacelle_humidity = prev["nacelle_humidity"] + random.uniform(-0.5, 0.5)
    nacelle_humidity = clamp(nacelle_humidity, 50.0, 60.0)

    if fault_mode == "overheat":
        temperature = clamp(temperature + random.uniform(12, 18), 35.0, 75.0)
        gearbox_temp = clamp(gearbox_temp + random.uniform(18, 30), 40.0, 95.0)
        vibration = clamp(vibration + random.uniform(3.5, 5.0), 0.3, 8.5)

    elif fault_mode == "high_vibration":
        vibration = clamp(vibration + random.uniform(2.0, 4.5), 0.3, 8.5)

    elif fault_mode == "hydraulic_drop":
        hydraulic_pressure = clamp(hydraulic_pressure - random.uniform(10, 18), 120.0, 182.0)

    return {
        "wind_speed": round(wind_speed, 2),
        "power_output": round(power_output, 1),
        "rotor_rpm": round(rotor_rpm, 2),
        "temperature": round(temperature, 1),
        "gearbox_temp": round(gearbox_temp, 1),
        "vibration": round(vibration, 2),
        "hydraulic_pressure": round(hydraulic_pressure, 1),
        "nacelle_humidity": round(nacelle_humidity, 1)
    }

def generate_series(length=300):
    data = []
    current = init_reading()

    fault_start = random.randint(90, 140)
    fault_length = random.randint(8, 15)
    fault_type = random.choice(["overheat", "high_vibration", "hydraulic_drop", None])

    for i in range(length):
        if i == 0:
            data.append(current)
            continue

        active_fault = None
        if fault_type is not None and fault_start <= i < fault_start + fault_length:
            active_fault = fault_type

        current = next_reading(current, fault_mode=active_fault)
        data.append(current)

    return data

if __name__ == "__main__":
    sensor_data = generate_series(300)

    output_path = os.path.join(os.path.dirname(__file__), "sensor_data.json")

    with open(output_path, "w") as f:
        json.dump(sensor_data, f, indent=2)

    print("Generated:", output_path)