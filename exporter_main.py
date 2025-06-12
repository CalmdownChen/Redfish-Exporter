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
    with open("TRack_exporter_config.yaml", "r") as file:
        config = yaml.safe_load(file)
    return config

config = load_config()
servers = config.get("servers", [])
psus = config.get("psus", [])
cdu_url = config.get("cdu_url", "")

sensors_dict = { # default None_A, if API call fail.
    'Temp_CPU0': 'None_A',  'Temp_CPU1': 'None_A',
    'Temp_CPU0_DIMMG0': 'None_A', 'Temp_CPU0_DIMMG1': 'None_A', 'Temp_CPU1_DIMMG0': 'None_A', 'Temp_CPU1_DIMMG1': 'None_A',
    #'Temp_SYS_Inlet': 'None_A', 'Temp_SYS_Outlet': 'None_A',
    'Temp_GPU_1': 'None_A', 'Temp_GPU_2': 'None_A', 'Temp_GPU_3': 'None_A', 'Temp_GPU_4': 'None_A',
    #'Temp_E3S1': 'None_A', 'Temp_E3S2': 'None_A', 'Temp_E3S3': 'None_A', 'Temp_E3S4': 'None_A',
    #'Temp_E3S5': 'None_A', 'Temp_E3S6': 'None_A', 'Temp_E3S7': 'None_A', 'Temp_E3S8': 'None_A',
    #'Temp_Disk3': 'None_A', 'Temp_Disk4': 'None_A', 'Temp_Disk5': 'None_A', 'Temp_Disk6': 'None_A',
    #'Temp_Disk7': 'None_A', 'Temp_Disk8': 'None_A', 'Temp_Disk9': 'None_A', 'Temp_Disk10': 'None_A',
}

#Prometheus metrics
sensor_temperature = Gauge("server_sensor_temperature_celsius", "Temperature from various sensors", ["server", "sensor_name"])
server_power = Gauge("server_power_watt", "Total power reading", ["server"])
server_fan_power = Gauge("server_fan_power_watt", "Total fan power reading", ["server"])
psu_power_output = Gauge("psu_output_power_watt", "Output power reading from PSU", ["psu_name"])
cdu_temperature = Gauge("cdu_temperature_celsius", "Temperature metrics from CDU", ["metric"])
cdu_pump = Gauge("cdu_pump_metric", "Pump metrics from CDU", ["metric"])
cdu_fan = Gauge("cdu_fan_metric", "Fan metrics from CDU", ["metric"])
cdu_sensor = Gauge("cdu_sensor_metric", "Sensor metrics from CDU", ["metric"])

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

                if sensor_name in sensors_dict:
                    if isinstance(value, (int, float)):
                        sensor_temperature.labels(server=ip, sensor_name=sensor_name).set(value)
                        print(f"[OK] {ip} {sensor_name} = {value}°C")
                    else:
                        sensor_temperature.labels(server=ip, sensor_name=sensor_name).set(0)
                        print(f"[SKIP] {ip} {sensor_name}: No Value ,state: {state}")

        except Exception as e:
            print(f"[ERROR] {ip} Thermal API error: {e}")

        # Server Node Power
        try:
            if "ASUS" in server['name']:
                server_power.labels(server=ip).set(0)
                continue
            r = requests.get(f"{base_url}/Chassis/Self/Sensors/Pwr_Node_Total",
                             headers={"Accept": "application/json"},
                             auth=auth, verify=False, timeout=15)
            r.raise_for_status()
            data = r.json()

            power = data.get("Reading")
            if isinstance(power, (int, float)):
                server_power.labels(server=ip).set(power)
                print(f"[OK] {ip} Node Power = {power}W")
            else:
                server_power.labels(server=ip).set(0)
                print(f"[SKIP] {ip} Node Power invalid value")

        except Exception as e:
            print(f"[ERROR] {ip} Node Power API error: {e}")

        # Server Fan Power
        try:
            if "ASUS" in server['name']:
                server_fan_power.labels(server=ip).set(0)
                continue
            r = requests.get(f"{base_url}/Chassis/Self/Sensors/Pwr_Fan_Total",
                             headers={"Accept": "application/json"},
                             auth=auth, verify=False, timeout=15)
            r.raise_for_status()
            data = r.json()

            power = data.get("Reading")
            if isinstance(power, (int, float)):
                server_fan_power.labels(server=ip).set(power)
                print(f"[OK] {ip} Fan Power = {power}W")
            else:
                server_fan_power.labels(server=ip).set(0)
                print(f"[SKIP] {ip} Fan Power invalid value")

        except Exception as e:
            print(f"[ERROR] {ip} Fan Power API error: {e}")



def fetch_psu_data():
    for psu in psus:
        try:
            response = requests.get(psu['apiUrl'], headers={'Accept': 'application/json'},
                                    auth=HTTPBasicAuth("root", "0penBmc"), verify=False, timeout=10)
            response_data = response.json()

            if "Reading" in response_data:
                value = response_data["Reading"]
                if isinstance(value, (int, float)):
                    psu_power_output.labels(psu_name=psu["name"]).set(value)
                    print(f"[OK] {psu['name']} = {value}W")
                else:
                    psu_power_output.labels(psu_name=psu["name"]).set(0)
                    print(f"[WARN] {psu['name']} No value")
            else:
                print(f"[WARN] {psu['name']} Sensor un-avaliable ")

        except Exception as e:
            print(f"[ERROR] {psu['name']} psu data get fail：{e}")
def fetch_cdu_data():
    """Query CDU metrics and expose them via Prometheus gauges."""

    try:
        response = requests.get(cdu_url, timeout=10)
        response.raise_for_status()
        src_data = response.json()

        for entry in src_data.get("responses", []):
            if not isinstance(entry, dict):
                continue

            for label, val in entry.items():
                if not isinstance(val, (int, float)):
                    print(f"[SKIP] CDU {label} invalid value")
                    continue

                if label.startswith("T_") or label == "Ta":
                    cdu_temperature.labels(metric=label).set(val)
                elif label.startswith("RPM_P") or label.startswith("POW_P") or label.startswith("PWM_P"):
                    cdu_pump.labels(metric=label).set(val)
                elif label.startswith("RPM_F") or label.startswith("POW_F") or label.startswith("PWM_F"):
                    cdu_fan.labels(metric=label).set(val)
                else:
                    cdu_sensor.labels(metric=label).set(val)

                print(f"[OK] CDU {label} = {val}")

    except Exception as e:
        print(f"[ERROR] CDU get data fail {e}")


if __name__ == '__main__':
    start_http_server(5000, addr="0.0.0.0")  # Prometheus get data from it
    while True:
        fetch_server_data()
        fetch_psu_data()
        fetch_cdu_data()
        time.sleep(15)  # update every 15 seconds
