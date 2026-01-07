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
    ["server_name", "sensor_name", "rack_name"],
)
memory_temperature = Gauge(
    "server_memory_temperature_celsius",
    "Memory temperature sensors",
    ["server_name", "sensor_name", "rack_name"],
)
gpu_temperature = Gauge(
    "server_gpu_temperature_celsius",
    "GPU temperature sensors",
    ["server_name", "sensor_name", "rack_name"],
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
server_power = Gauge(
    "server_power_watt", "Total power reading", ["server", "rack_name"]
)
server_fan_power = Gauge(
    "server_fan_power_watt", "Total fan power reading", ["server", "rack_name"]
)
server_cpu_power = Gauge(
    "server_cpu_power_watt", "Total CPU power reading", ["server", "rack_name"]
)
server_gpu_power = Gauge(
    "server_gpu_power_watt", "Total GPU power reading", ["server", "rack_name"]
)
server_mem_power = Gauge(
    "server_mem_power_watt", "Total memory power reading", ["server", "rack_name"]
)
psu_power_output = Gauge(
    "psu_output_power_watt",
    "Output power reading from PSU",
    ["psu_name", "rack_name"],
)
powershelf_psu_fail = Gauge(
    "powershelf_psu_fail",
    "PSU health status (0: OK, 1: Not OK)",
    ["sensor_name", "rack_name"],
)
powershelf_chassis_fail = Gauge(
    "powershelf_chassis_fail",
    "PSU chassis input health status (0: OK, 1: Not OK)",
    ["sensor_name", "rack_name"],
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
cdu_tank_level = Gauge(
    "cdu_tank_level",
    "Water tank level status from CDU",
    ["sensor_name", "rack_name"],
)
cdu_leakage = Gauge(
    "cdu_leakage",
    "Leakage sensor readings from CDU",
    ["sensor_name", "rack_name"],
)
cdu_pump_fail = Gauge(
    "cdu_pump_fail",
    "CDU pump failure status (1: fail, 0: ok)",
    ["sensor_name", "rack_name"],
)
cdu_fan_fail = Gauge(
    "cdu_fan_fail",
    "CDU fan failure status (1: fail, 0: ok)",
    ["sensor_name", "rack_name"],
)

# Calculated CDU metrics
cdu_calculated = Gauge("cdu_calculated_metric", "Calculated metrics from CDU", ["metric"])

# Global variable for total PSU power
total_psu_power = 0.0

# Track consecutive PSU and chassis status fetch failures to avoid immediately marking sensors as failed
status_failures = {}


def write_sensor_snapshot(nodes_data, psu_data, cdu_data):
    """Persist the latest sensor readings to a JSON file without altering exporter behavior."""

    snapshot = {
        "nodes": nodes_data,
        "Powershelf": psu_data,
        "CDU": cdu_data,
        "meta": {
            "exporter_version": "",  # 預留未來寫入
            "rack_id": "",  # 預留未來寫入
        },
    }

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensors_snapshot.json")

    try:
        with open(output_path, "w", encoding="utf-8") as outfile:
            json.dump(snapshot, outfile, ensure_ascii=False, indent=2)
        print(f"[OK] Sensor snapshot written to {output_path}")
    except Exception as exc:
        print(f"[ERROR] Failed to write sensor snapshot: {exc}")

def fetch_server_data():
    nodes_data = {}

    for server in servers:
        ip = server['ip_address']
        rack_name = server.get('rack_name', 'unknown')
        server_label = server.get('name', ip)
        base_url = f"https://{ip}/redfish/v1"
        auth = HTTPBasicAuth("admin", "password")

        server_entry = {
            "bmc_ip": ip,
            "name": server_label,
            "sensors": {},
        }

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
                unit = item.get("ReadingUnits", "C")

                server_entry["sensors"][sensor_name] = {"value": value, "unit": unit}

                gauge = sensor_gauge_map.get(sensor_name)
                if gauge:
                    if isinstance(value, (int, float)):
                        gauge.labels(
                            server_name=ip,
                            sensor_name=sensor_name,
                            rack_name=rack_name,
                        ).set(value)
                        print(f"[OK] {ip} {sensor_name} = {value}°C")
                    else:
                        gauge.labels(
                            server_name=ip,
                            sensor_name=sensor_name,
                            rack_name=rack_name,
                        ).set(0)
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
                unit = data.get("ReadingUnits", "W")

                server_entry["sensors"][sensor] = {"value": power, "unit": unit}

                if isinstance(power, (int, float)):
                    gauge.labels(server=ip, rack_name=rack_name).set(power)
                    print(f"[OK] {ip} {sensor} = {power}W")
                else:
                    gauge.labels(server=ip, rack_name=rack_name).set(0)
                    print(f"[SKIP] {ip} {sensor} invalid value")
            except Exception as e:
                print(f"[ERROR] {ip} {sensor} API error: {e}")

        nodes_data[server_label] = server_entry

    return nodes_data



def fetch_psu_data():
    global total_psu_power
    total_psu_power = 0.0
    psu_data = {}
    for psu in psus:
        base_url = f"https://{psu['ip_address']}/redfish/v1"
        rack_name = psu.get("rack_name", "unknown")
        psu_entry = {}

        try:
            response = requests.get(
                f"{base_url}/Chassis/chassis/Sensors/chassis_output_power",
                headers={'Accept': 'application/json'},
                auth=HTTPBasicAuth("root", "0penBmc"),
                verify=False,
                timeout=10,
            )
            response_data = response.json()

            if "Reading" in response_data:
                value = response_data["Reading"]
                unit = response_data.get("ReadingUnits", "W")
                psu_entry["Output_Power"] = {"value": value, "unit": unit}
                if isinstance(value, (int, float)):
                    psu_power_output.labels(
                        psu_name=psu["name"], rack_name=rack_name
                    ).set(value)
                    total_psu_power += value
                    print(f"[OK] {psu['name']} = {value}W")
                else:
                    psu_power_output.labels(
                        psu_name=psu["name"], rack_name=rack_name
                    ).set(0)
                    print(f"[WARN] {psu['name']} No value")
            else:
                print(f"[WARN] {psu['name']} Sensor un-avaliable ")
        except Exception as e:
            print(f"[ERROR] {psu['name']} output power get fail：{e}")

        for i in range(1, 13):
            sensor_key = ("psu", psu["name"], i)
            try:
                status_resp = requests.get(
                    f"{base_url}/Chassis/chassis/Power/Oem/tsmc/PSU{i}",
                    headers={'Accept': 'application/json'},
                    auth=HTTPBasicAuth("root", "0penBmc"),
                    verify=False,
                    timeout=10,
                )
                status_data = status_resp.json()
                health = status_data.get('Status', {}).get('Health')
                metric_value = 0 if health == "OK" else 1
                psu_entry[f"PSU_{i}_Health"] = {"value": health, "unit": None}
                powershelf_psu_fail.labels(
                    sensor_name=f"PSU_{i}",
                    rack_name=rack_name,
                ).set(metric_value)
                if health == "OK":
                    print(f"[OK] {psu['name']} PSU_{i} Health {health}")
                else:
                    print(f"[WARN] {psu['name']} PSU_{i} Health {health}")
                status_failures[sensor_key] = 0
            except Exception as e:
                failure_count = status_failures.get(sensor_key, 0) + 1
                status_failures[sensor_key] = failure_count
                if failure_count >= 2:
                    powershelf_psu_fail.labels(
                        sensor_name=f"PSU_{i}",
                        rack_name=rack_name,
                    ).set(1)
                    print(
                        f"[ERROR] {psu['name']} PSU_{i} status get fail (consecutive {failure_count})：{e}"
                    )
                else:
                    print(
                        f"[ERROR] {psu['name']} PSU_{i} status get fail：{e} (will retry)"
                    )

        for chassis_label, sensor_name in [
            ("Chassis_A", "chassis_A_input_Voltage"),
            ("Chassis_B", "chassis_B_input_Voltage"),
        ]:
            sensor_key = ("chassis", psu["name"], chassis_label)
            try:
                chassis_resp = requests.get(
                    f"{base_url}/Chassis/chassis/Sensors/{sensor_name}",
                    headers={'Accept': 'application/json'},
                    auth=HTTPBasicAuth("root", "0penBmc"),
                    verify=False,
                    timeout=10,
                )
                chassis_data = chassis_resp.json()
                health = chassis_data.get("Status", {}).get("Health")
                metric_value = 0 if health == "OK" else 1
                psu_entry[f"{chassis_label}_Health"] = {"value": health, "unit": None}
                powershelf_chassis_fail.labels(
                    sensor_name=chassis_label,
                    rack_name=rack_name,
                ).set(metric_value)
                status_failures[sensor_key] = 0
                if health == "OK":
                    print(f"[OK] {psu['name']} {chassis_label} Health {health}")
                else:
                    print(f"[WARN] {psu['name']} {chassis_label} Health {health}")
            except Exception as e:
                failure_count = status_failures.get(sensor_key, 0) + 1
                status_failures[sensor_key] = failure_count
                if failure_count >= 2:
                    powershelf_chassis_fail.labels(
                        sensor_name=chassis_label,
                        rack_name=rack_name,
                    ).set(1)
                    print(
                        f"[ERROR] {psu['name']} {chassis_label} status get fail (consecutive {failure_count})：{e}"
                    )
                else:
                    print(
                        f"[ERROR] {psu['name']} {chassis_label} status get fail：{e} (will retry)"
                    )
        psu_data[psu["name"]] = psu_entry
    return psu_data
def fetch_cdu_data():
    """Query CDU metrics for each configured CDU and expose them via Prometheus gauges."""

    global total_psu_power

    cdu_data = {}
    test_input_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "cud_test_input_no_release.json",
    )

    for cdu in cdus:
        url = cdu.get("url")
        rack_name = cdu.get("rack_name", "unknown")
        if not url:
            continue

        cdu_entry = {
            "Temperature": {},
            "Pump": {},
            "Fan": {},
            "Sensor": {},
            "TankLevel": {},
            "Leakage": {},
            "Calculated": {},
        }

        try:
            if os.path.exists(test_input_path):
                with open(test_input_path, "r", encoding="utf-8") as infile:
                    src_data = json.load(infile)
                print(f"[INFO] {rack_name} using test input {test_input_path}")
            else:
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
            tank_level_sensors = {
                "Sensor_LEVH": None,
                "Sensor_LEVM": None,
                "Sensor_LEVL": None,
            }
            pump_rpm = {}
            pump_pwm = {}
            fan_rpm = {}
            fan_pwm = {}

            for entry in src_data.get("responses", []):
                if not isinstance(entry, dict):
                    continue

                for label, val in entry.items():
                    if not isinstance(val, (int, float)):
                        print(f"[SKIP] {rack_name} {label} invalid value")
                        continue

                    if label in leakage_values:
                        leakage_values[label] = val
                    if label in tank_level_sensors:
                        tank_level_sensors[label] = val

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
                        cdu_entry["Temperature"][label] = {"value": val, "unit": "C"}
                    elif label.startswith("RPM_P") or label.startswith("POW_P") or label.startswith("PWM_P"):
                        cdu_pump.labels(metric=label, rack_name=rack_name).set(val)
                        cdu_entry["Pump"][label] = {"value": val, "unit": None}
                        if label.startswith("RPM_P"):
                            pump_rpm[label.replace("RPM_P", "")] = val
                        elif label.startswith("PWM_P"):
                            pump_pwm[label.replace("PWM_P", "")] = val
                    elif label.startswith("RPM_F") or label.startswith("POW_F") or label.startswith("PWM_F"):
                        cdu_fan.labels(metric=label, rack_name=rack_name).set(val)
                        cdu_entry["Fan"][label] = {"value": val, "unit": None}
                        if label.startswith("RPM_F"):
                            fan_rpm[label.replace("RPM_F", "")] = val
                        elif label.startswith("PWM_F"):
                            fan_pwm[label.replace("PWM_F", "")] = val
                    else:
                        cdu_sensor.labels(metric=label, rack_name=rack_name).set(val)
                        cdu_entry["Sensor"][label] = {"value": val, "unit": None}

                    print(f"[OK] {rack_name} {label} = {val}")

            leak_count = sum(1 for v in leakage_values.values() if v == 1)
            if leak_count >= 2:
                for sensor_name, value in leakage_values.items():
                    cdu_leakage.labels(sensor_name=sensor_name, rack_name=rack_name).set(
                        0 if value is None else value
                    )
                    cdu_leakage.labels(
                        sensor_name=sensor_name,
                        rack_name=f"keep-watching-{rack_name}",
                    ).set(0)
                    cdu_entry["Leakage"][sensor_name] = {"value": value, "unit": None}
            elif leak_count == 1:
                for sensor_name, value in leakage_values.items():
                    cdu_leakage.labels(sensor_name=sensor_name, rack_name=rack_name).set(0)
                    cdu_leakage.labels(
                        sensor_name=sensor_name,
                        rack_name=f"keep-watching-{rack_name}",
                    ).set(0 if value is None else value)
                    cdu_entry["Leakage"][sensor_name] = {"value": value, "unit": None}
            else:
                for sensor_name in leakage_values:
                    cdu_leakage.labels(sensor_name=sensor_name, rack_name=rack_name).set(0)
                    cdu_leakage.labels(
                        sensor_name=sensor_name,
                        rack_name=f"keep-watching-{rack_name}",
                    ).set(0)
                    cdu_entry["Leakage"][sensor_name] = {"value": leakage_values.get(sensor_name), "unit": None}

            # Evaluate tank level conditions
            sensor_levh = tank_level_sensors.get("Sensor_LEVH")
            sensor_levm = tank_level_sensors.get("Sensor_LEVM")
            sensor_levl = tank_level_sensors.get("Sensor_LEVL")

            level_medium = level_low = critical_low = 0
            if sensor_levl == 0:
                critical_low = 1
            elif sensor_levm == 0:
                level_low = 1
            elif sensor_levh == 0:
                level_medium = 1

            cdu_tank_level.labels(sensor_name="Level_Medium", rack_name=rack_name).set(
                level_medium
            )
            cdu_tank_level.labels(sensor_name="Level_Low", rack_name=rack_name).set(level_low)
            cdu_tank_level.labels(sensor_name="Critical_Low", rack_name=rack_name).set(
                critical_low
            )

            cdu_entry["TankLevel"].update(
                {
                    "Level_Medium": {"value": level_medium, "unit": None},
                    "Level_Low": {"value": level_low, "unit": None},
                    "Critical_Low": {"value": critical_low, "unit": None},
                }
            )

            # Evaluate pump failure conditions
            for idx in set(pump_rpm) | set(pump_pwm):
                rpm = pump_rpm.get(idx)
                pwm = pump_pwm.get(idx)
                fail = 1 if rpm is not None and pwm is not None and rpm < 100 and pwm != 0 else 0
                cdu_pump_fail.labels(sensor_name=f"Pump_{idx}", rack_name=rack_name).set(fail)
                cdu_entry["Pump"][f"Pump_{idx}_Fail"] = {"value": fail, "unit": None}

            # Evaluate fan failure conditions
            for idx in set(fan_rpm) | set(fan_pwm):
                rpm = fan_rpm.get(idx)
                pwm = fan_pwm.get(idx)
                fail = 1 if rpm is not None and pwm is not None and rpm < 100 and pwm != 0 else 0
                cdu_fan_fail.labels(sensor_name=f"Fan_{idx}", rack_name=rack_name).set(fail)
                cdu_entry["Fan"][f"Fan_{idx}_Fail"] = {"value": fail, "unit": None}

            # Calculate additional metrics if all required values are available
            if all(v is not None for v in (t_wi, t_wo)) and total_psu_power:
                lpm_w = (total_psu_power / 0.97) / 69.7833 / (t_wo - t_wi)
                lpm_w_rounded = round(lpm_w, 2)
                cdu_calculated.labels(metric="LPM_W").set(lpm_w_rounded)
                print(f"[OK] {rack_name} LPM_W = {lpm_w_rounded:.2f}")
                cdu_entry["Calculated"]["LPM_W"] = {"value": lpm_w_rounded, "unit": None}

            if all(v is not None for v in (t_cr, t_cco)) and total_psu_power:
                lpm_c = total_psu_power / 69.7833 / (t_cr - t_cco)
                lpm_c_rounded = round(lpm_c, 2)
                cdu_calculated.labels(metric="LPM_C").set(lpm_c_rounded)
                print(f"[OK] {rack_name} LPM_C = {lpm_c_rounded:.2f}")
                cdu_entry["Calculated"]["LPM_C"] = {"value": lpm_c_rounded, "unit": None}
            else:
                lpm_c = None

            if lpm_c is not None and t_cco is not None and t_cci is not None:
                heat_cc = lpm_c * (t_cco - t_cci) * 69.7833
                heat_cc_rounded = round(heat_cc, 2)
                cdu_calculated.labels(metric="Heat_CC").set(heat_cc_rounded)
                print(f"[OK] {rack_name} Heat_CC = {heat_cc_rounded:.2f}")
                cdu_entry["Calculated"]["Heat_CC"] = {"value": heat_cc_rounded, "unit": None}

        except Exception as e:
            print(f"[ERROR] {rack_name} get data fail {e}")

        cdu_data[rack_name] = cdu_entry

    return cdu_data


if __name__ == '__main__':
    start_http_server(5000, addr="0.0.0.0")  # Prometheus get data from it
    while True:
        nodes_snapshot = fetch_server_data()
        psu_snapshot = fetch_psu_data()
        cdu_snapshot = fetch_cdu_data()
        write_sensor_snapshot(nodes_snapshot, psu_snapshot, cdu_snapshot)
        time.sleep(15)  # update every 15 seconds
