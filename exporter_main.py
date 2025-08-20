import os
import time
import json
import requests
import subprocess
import yaml
from requests.auth import HTTPBasicAuth
from prometheus_client import Gauge
from prometheus_client import start_http_server

def load_config():
    """Load exporter configuration from a YAML file.

    The configuration file is expected to reside in the same directory as this
    script. Using an absolute path ensures the file can be located when the
    script is executed via systemd or from another working directory.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "exporter_config.yaml")
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)
    return config

config = load_config()
servers = config.get("servers", [])
psus = config.get("psus", [])
cdus = config.get("cdu", [])

# Prometheus metrics for various component temperatures
cpu_temperature = Gauge(
    "server_cpu_temperature_celsius",
    "CPU temperature sensors",
    ["server_name", "sensor_name"],
)
memory_temperature = Gauge(
    "server_memory_temperature_celsius",
    "Memory temperature sensors",
    ["server_name", "sensor_name"],
)
gpu_temperature = Gauge(
    "server_gpu_temperature_celsius",
    "GPU temperature sensors",
    ["server_name", "sensor_name"],
)

# Mapping sensor names to corresponding gauges
sensor_gauge_map = {
    "Temp_CPU0": cpu_temperature,
    "Temp_CPU1": cpu_temperature,
    "Temp_CPU0_DIMMG0": memory_temperature,
    "Temp_CPU0_DIMMG1": memory_temperature,
    "Temp_CPU1_DIMMG0": memory_temperature,
    "Temp_CPU1_DIMMG1": memory_temperature,
    "Temp_GPU_1": gpu_temperature,
    "Temp_GPU_2": gpu_temperature,
    "Temp_GPU_3": gpu_temperature,
    "Temp_GPU_4": gpu_temperature,
}

# Prometheus metrics
server_power = Gauge("server_power_watt", "Total power reading", ["server"])
server_fan_power = Gauge("server_fan_power_watt", "Total fan power reading", ["server"])
server_cpu_power = Gauge("server_cpu_power_watt", "Total CPU power reading", ["server"])
server_gpu_power = Gauge("server_gpu_power_watt", "Total GPU power reading", ["server"])
server_mem_power = Gauge("server_mem_power_watt", "Total memory power reading", ["server"])
psu_power_output = Gauge(
    "psu_output_power_watt",
    "Output power reading from PSU",
    ["psu_name", "rack_name"],
)
cdu_temperature = Gauge(
    "cdu_temperature_celsius",
    "Temperature metrics from CDU",
    ["metric", "rack_name"],
)
cdu_pump = Gauge(
    "cdu_pump_metric",
    "Pump metrics from CDU",
    ["metric", "rack_name"],
)
cdu_fan = Gauge(
    "cdu_fan_metric",
    "Fan metrics from CDU",
    ["metric", "rack_name"],
)
cdu_sensor = Gauge(
    "cdu_sensor_metric",
    "Sensor metrics from CDU",
    ["metric", "rack_name"],
)
cdu_leakage = Gauge(
    "cdu_leakage",
    "Leakage sensor readings from CDU",
    ["sensor_name", "rack_name"],
)

# Calculated CDU metrics
cdu_calculated = Gauge("cdu_calculated_metric", "Calculated metrics from CDU", ["metric"])

# Global variable for total PSU power
total_psu_power = 0.0

def fetch_server_data():
    for server in servers:
        ip = server['ip_address']
        base_url = f"https://{ip}/redfish/v1"
        auth = HTTPBasicAuth("admin", "password")

        # Thermal sensor
        try:
            r = requests.get(f"{base_url}/Chassis/Self/Thermal",
                             headers={"Accept": "application/json"},
                             auth=auth, verify=False, timeout=15)
            r.raise_for_status()
            data = r.json()

            for item in data.get("Temperatures", []):
                sensor_name = item.get("Name", "Unknown")
                value = item.get("ReadingCelsius")
                state = item.get("Status", {}).get("State", "Unknown")

                gauge = sensor_gauge_map.get(sensor_name)
                if gauge:
                    if isinstance(value, (int, float)):
                        gauge.labels(server_name=ip, sensor_name=sensor_name).set(value)
                        print(f"[OK] {ip} {sensor_name} = {value}°C")
                    else:
                        gauge.labels(server_name=ip, sensor_name=sensor_name).set(0)
                        print(f"[SKIP] {ip} {sensor_name}: No Value ,state: {state}")

        except Exception as e:
            print(f"[ERROR] {ip} Thermal API error: {e}")

        # Server power metrics
        power_metrics = {
            "Pwr_Node_Total": server_power,
            "Pwr_Fan_Total": server_fan_power,
            "Pwr_CPU_Total": server_cpu_power,
            "Pwr_GPU_Total": server_gpu_power,
            "Pwr_Mem_Total": server_mem_power,
        }

        if "ASUS" in server['name']:
            for gauge in power_metrics.values():
                gauge.labels(server=ip).set(0)
            continue

        for sensor, gauge in power_metrics.items():
            try:
                r = requests.get(
                    f"{base_url}/Chassis/Self/Sensors/{sensor}",
                    headers={"Accept": "application/json"},
                    auth=auth,
                    verify=False,
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json()

                power = data.get("Reading")
                if isinstance(power, (int, float)):
                    gauge.labels(server=ip).set(power)
                    print(f"[OK] {ip} {sensor} = {power}W")
                else:
                    gauge.labels(server=ip).set(0)
                    print(f"[SKIP] {ip} {sensor} invalid value")
            except Exception as e:
                print(f"[ERROR] {ip} {sensor} API error: {e}")



def fetch_psu_data():
    global total_psu_power
    total_psu_power = 0.0
    for psu in psus:
        try:
            response = requests.get(
                psu['apiUrl'],
                headers={'Accept': 'application/json'},
                auth=HTTPBasicAuth("root", "0penBmc"),
                verify=False,
                timeout=10,
            )
            response_data = response.json()

            if "Reading" in response_data:
                value = response_data["Reading"]
                if isinstance(value, (int, float)):
                    psu_power_output.labels(
                        psu_name=psu["name"], rack_name=psu.get("rack_name", "unknown")
                    ).set(value)
                    total_psu_power += value
                    print(f"[OK] {psu['name']} = {value}W")
                else:
                    psu_power_output.labels(
                        psu_name=psu["name"], rack_name=psu.get("rack_name", "unknown")
                    ).set(0)
                    print(f"[WARN] {psu['name']} No value")
            else:
                print(f"[WARN] {psu['name']} Sensor un-avaliable ")

        except Exception as e:
            print(f"[ERROR] {psu['name']} psu data get fail：{e}")
def fetch_cdu_data():
    """Query CDU metrics for each configured CDU and expose them via Prometheus gauges."""

    global total_psu_power

    for cdu in cdus:
        url = cdu.get("url")
        rack_name = cdu.get("rack_name", "unknown")
        if not url:
            continue

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            src_data = response.json()

            t_wi = t_wo = t_cco = t_cci = t_cr = None
            leakage_values = {
                "Sensor_L1": None,
                "Sensor_L2": None,
                "Sensor_RL1": None,
                "Sensor_RL2": None,
            }

            for entry in src_data.get("responses", []):
                if not isinstance(entry, dict):
                    continue

                for label, val in entry.items():
                    if not isinstance(val, (int, float)):
                        print(f"[SKIP] {rack_name} {label} invalid value")
                        continue

                    if label in leakage_values:
                        leakage_values[label] = val

                    if label == "T_WI":
                        t_wi = val
                    elif label == "T_WO":
                        t_wo = val
                    elif label == "T_CCO":
                        t_cco = val
                    elif label == "T_CCI":
                        t_cci = val
                    elif label == "T_CR":
                        t_cr = val

                    if label.startswith("T_") or label == "Ta":
                        cdu_temperature.labels(metric=label, rack_name=rack_name).set(val)
                    elif label.startswith("RPM_P") or label.startswith("POW_P") or label.startswith("PWM_P"):
                        cdu_pump.labels(metric=label, rack_name=rack_name).set(val)
                    elif label.startswith("RPM_F") or label.startswith("POW_F") or label.startswith("PWM_F"):
                        cdu_fan.labels(metric=label, rack_name=rack_name).set(val)
                    else:
                        cdu_sensor.labels(metric=label, rack_name=rack_name).set(val)

                    print(f"[OK] {rack_name} {label} = {val}")

            leak_count = sum(1 for v in leakage_values.values() if v == 1)
            if leak_count >= 2:
                for sensor_name, value in leakage_values.items():
                    cdu_leakage.labels(sensor_name=sensor_name, rack_name=rack_name).set(
                        0 if value is None else value
                    )
                for sensor_name in leakage_values:
                    cdu_leakage.labels(
                        sensor_name=sensor_name,
                        rack_name=f"keep_watching_{rack_name}",
                    ).set(0)
            elif leak_count == 1:
                for sensor_name, value in leakage_values.items():
                    cdu_leakage.labels(sensor_name=sensor_name, rack_name=rack_name).set(0)
                    cdu_leakage.labels(
                        sensor_name=sensor_name,
                        rack_name=f"keep_watching_{rack_name}",
                    ).set(0 if value is None else value)
            else:
                for sensor_name in leakage_values:
                    cdu_leakage.labels(sensor_name=sensor_name, rack_name=rack_name).set(0)
                    cdu_leakage.labels(
                        sensor_name=sensor_name,
                        rack_name=f"keep_watching_{rack_name}",
                    ).set(0)

            # Calculate additional metrics if all required values are available
            if all(v is not None for v in (t_wi, t_wo)) and total_psu_power:
                lpm_w = (total_psu_power / 0.97) / 69.7833 / (t_wo - t_wi)
                lpm_w_rounded = round(lpm_w, 2)
                cdu_calculated.labels(metric="LPM_W").set(lpm_w_rounded)
                print(f"[OK] {rack_name} LPM_W = {lpm_w_rounded:.2f}")

            if all(v is not None for v in (t_cr, t_cco)) and total_psu_power:
                lpm_c = total_psu_power / 69.7833 / (t_cr - t_cco)
                lpm_c_rounded = round(lpm_c, 2)
                cdu_calculated.labels(metric="LPM_C").set(lpm_c_rounded)
                print(f"[OK] {rack_name} LPM_C = {lpm_c_rounded:.2f}")
            else:
                lpm_c = None

            if lpm_c is not None and t_cco is not None and t_cci is not None:
                heat_cc = lpm_c * (t_cco - t_cci) * 69.7833
                heat_cc_rounded = round(heat_cc, 2)
                cdu_calculated.labels(metric="Heat_CC").set(heat_cc_rounded)
                print(f"[OK] {rack_name} Heat_CC = {heat_cc_rounded:.2f}")

        except Exception as e:
            print(f"[ERROR] {rack_name} get data fail {e}")


if __name__ == '__main__':
    start_http_server(5000, addr="0.0.0.0")  # Prometheus get data from it
    while True:
        fetch_server_data()
        fetch_psu_data()
        fetch_cdu_data()
        time.sleep(15)  # update every 15 seconds
