"""CDU Collector"""

import httpx
from prometheus_client.core import GaugeMetricFamily

from tsre.core.logger.log import get_logger
from src.collectors.base import BaseCollector
from src.utils.http_client import HttpClient
from config.setting import Settings

logger = get_logger("exporter_logger")
setting = Settings()


class CduCollector(BaseCollector):
    def __init__(self) -> None:
        super().__init__()
        self.server_list = setting.cdu_list
        self.auth = (setting.cdu_account, setting.cdu_pwd)
        labels = ["sensor_name", "server_name", "rack_name"]
        self.metrics_dict = {
            "cdu_temperature_celsius": GaugeMetricFamily(
                setting.metric_prefix + "cdu_temperature_celsius",
                "Temperature metrics from CDU",
                labels=labels,
            ),
            "cdu_pump_metric": GaugeMetricFamily(
                setting.metric_prefix + "cdu_pump_metric",
                "Pump metrics from CDU",
                labels=labels,
            ),
            "cdu_fan_metric": GaugeMetricFamily(
                setting.metric_prefix + "cdu_fan_metric",
                "Fan metrics from CDU",
                labels=labels,
            ),
            "cdu_sensor_metric": GaugeMetricFamily(
                setting.metric_prefix + "cdu_sensor_metric",
                "Sensor metrics from CDU",
                labels=labels,
            ),
            "cdu_tank_level": GaugeMetricFamily(
                setting.metric_prefix + "cdu_tank_level",
                "Water tank level status from CDU",
                labels=labels,
            ),
            "cdu_leakage": GaugeMetricFamily(
                setting.metric_prefix + "cdu_leakage",
                "Leakage sensor readings from CDU",
                labels=labels,
            ),
            "cdu_pump_fail": GaugeMetricFamily(
                setting.metric_prefix + "cdu_pump_fail",
                "CDU pump failure status (1: fail, 0: ok)",
                labels=labels,
            ),
            "cdu_fan_fail": GaugeMetricFamily(
                setting.metric_prefix + "cdu_fan_fail",
                "CDU fan failure status (1: fail, 0: ok)",
                labels=labels,
            ),
        }

    async def collect_metrics(
        self, client: httpx.AsyncClient, semaphore, server, _seq
    ):
        async with semaphore:
            url = f"http://{server['ip']}/getall"
            data = {}  #type: ignore
            try:
                data = await HttpClient.get(
                    client, url, self.auth
                )  # type: ignore
            except Exception as e:
                logger.error(e)
            if not data:
                logger.error(f"no data: {server['ip']}")
                return

            state = {
                "leakage_values": {
                    "Sensor_L1": 0,
                    "Sensor_L2": 0,
                    "Sensor_RL1": 0,
                    "Sensor_RL2": 0,
                },
                "tank_level_sensors": {
                    "Sensor_LEVH": None,
                    "Sensor_LEVM": None,
                    "Sensor_LEVL": None,
                },
                "pump_rpm": {},
                "pump_pwm": {},
                "fan_rpm": {},
                "fan_pwm": {},
            }

            for entry in data.get("responses", []):
                if not isinstance(entry, dict):
                    logger.info(f"failed: {entry}")
                    continue

                for label, value in entry.items():
                    self._update_sensor_state(label, value, state)
                    self._classify_and_record_metric(
                        label, value, server, state
                    )
                    logger.info(f"[OK] {server['location']} {label} = {value}")

            self.process_cdu_leakage(server, state["leakage_values"])
            self.process_cdu_tank_level(server, state["tank_level_sensors"])
            self.process_cdu_pump_fail(
                server, state["pump_rpm"], state["pump_pwm"]
            )
            self.process_cdu_fan_fail(
                server, state["fan_rpm"], state["fan_pwm"]
            )

    def _update_sensor_state(self, label, value, state):
        if label in state["leakage_values"]:
            state["leakage_values"][label] = value
        elif label in state["tank_level_sensors"]:
            state["tank_level_sensors"][label] = value

    def _classify_and_record_metric(self, label, value, server, state):
        if self._is_temperature_metric(label):
            metric_name = "cdu_temperature_celsius"
        elif self._is_pump_metric(label):
            metric_name = "cdu_pump_metric"
            self._update_pump_state(label, value, state)
        elif self._is_fan_metric(label):
            metric_name = "cdu_fan_metric"
            self._update_fan_state(label, value, state)
        else:
            metric_name = "cdu_sensor_metric"

        self.add_metric(
            self.metrics_dict[metric_name],
            server["ip"],
            server["location"],
            label,
            value,
        )

    def _is_temperature_metric(self, label):
        return label.startswith("T_") or label == "Ta"

    def _is_pump_metric(self, label):
        return (
            label.startswith("RPM_P")
            or label.startswith("POW_P")
            or label.startswith("PWM_P")
        )

    def _is_fan_metric(self, label):
        return (
            label.startswith("RPM_F")
            or label.startswith("POW_F")
            or label.startswith("PWM_F")
        )

    def _update_pump_state(self, label, value, state):
        if label.startswith("RPM_P"):
            state["pump_rpm"][label.replace("RPM_P", "")] = value
        elif label.startswith("PWM_P"):
            state["pump_pwm"][label.replace("PWM_P", "")] = value

    def _update_fan_state(self, label, value, state):
        if label.startswith("RPM_F"):
            state["fan_rpm"][label.replace("RPM_F", "")] = value
        elif label.startswith("PWM_F"):
            state["fan_pwm"][label.replace("PWM_F", "")] = value

    def process_cdu_leakage(self, server, leakage_values):
        leak_count = sum(leakage_values.values())
        for sensor_name, value in leakage_values.items():
            self.add_metric(
                self.metrics_dict["cdu_leakage"],
                server["ip"],
                server["location"],
                sensor_name,
                value if leak_count >= 2 else 0,
            )
            self.add_metric(
                self.metrics_dict["cdu_leakage"],
                "keep-watching-" + server["ip"],
                server["location"],
                sensor_name,
                value if leak_count == 1 else 0,
            )

    def process_cdu_tank_level(self, server, tank_level_sensors):
        level_medium = level_low = critical_low = 0
        if tank_level_sensors["Sensor_LEVL"] == 0:
            critical_low = 1
        elif tank_level_sensors["Sensor_LEVM"] == 0:
            level_low = 1
        elif tank_level_sensors["Sensor_LEVH"] == 0:
            level_medium = 1

        self.add_metric(
            self.metrics_dict["cdu_tank_level"],
            server["ip"],
            server["location"],
            "Level_Medium",
            level_medium,
        )
        self.add_metric(
            self.metrics_dict["cdu_tank_level"],
            server["ip"],
            server["location"],
            "Level_Low",
            level_low,
        )
        self.add_metric(
            self.metrics_dict["cdu_tank_level"],
            server["ip"],
            server["location"],
            "Critical_Low",
            critical_low,
        )

    def process_cdu_pump_fail(self, server, pump_rpm, pump_pwm):
        # Evaluate pump failure conditions
        for idx in set(pump_rpm) | set(pump_pwm):
            rpm = pump_rpm.get(idx)
            pwm = pump_pwm.get(idx)
            fail = (
                1
                if rpm is not None
                and pwm is not None
                and rpm < 100
                and pwm != 0
                else 0
            )
            self.add_metric(
                self.metrics_dict["cdu_pump_fail"],
                server["ip"],
                server["location"],
                f"Pump_{idx}",
                fail,
            )

    def process_cdu_fan_fail(self, server, fan_rpm, fan_pwm):
        # Evaluate fan failure conditions
        for idx in set(fan_rpm) | set(fan_pwm):
            rpm = fan_rpm.get(idx)
            pwm = fan_pwm.get(idx)
            fail = (
                1
                if rpm is not None
                and pwm is not None
                and rpm < 100
                and pwm != 0
                else 0
            )
            self.add_metric(
                self.metrics_dict["cdu_fan_fail"],
                server["ip"],
                server["location"],
                f"Fan_{idx}",
                fail,
            )
