"""Powershelf Collector"""

import httpx
from prometheus_client.core import GaugeMetricFamily

from tsre.core.logger.log import get_logger
from src.collectors.base import BaseCollector
from src.utils.http_client import HttpClient
from config.setting import Settings

logger = get_logger("exporter_logger")
setting = Settings()


class PowershelfCollector(BaseCollector):
    def __init__(self) -> None:
        super().__init__()
        self.server_list = setting.powershelf_list
        self.auth = (setting.powershelf_account, setting.powershelf_pwd)
        labels = ["sensor_name", "server_name", "rack_name", "psu_name"]
        self.metrics_dict = {
            "psu_output_power_watt": GaugeMetricFamily(
                setting.metric_prefix + "psu_output_power_watt",
                "Output power reading from PSU",
                labels=labels,
            ),
            "powershelf_psu_fail": GaugeMetricFamily(
                setting.metric_prefix + "powershelf_psu_fail",
                "PSU health status (0: OK, 1: Not OK)",
                labels=labels,
            ),
            "powershelf_chassis_fail": GaugeMetricFamily(
                setting.metric_prefix + "powershelf_chassis_fail",
                "PSU chassis input health status (0: OK, 1: Not OK)",
                labels=labels,
            ),
        }

    async def collect_metrics(
        self, client: httpx.AsyncClient, semaphore, server, seq
    ):
        async with semaphore:
            server["name"] = "psuv_output"
            if seq > 0:
                server["name"] = f"psf{seq}" + server["name"]

            try:
                await self.collect_chassis_output_power(client, server)
                for i in range(1, 13):
                    await self.collect_psu_health(client, server, i)
                await self.collect_chassis_a_health(client, server)
                await self.collect_chassis_b_health(client, server)
            except Exception as e:
                logger.error(e)

    async def collect_chassis_output_power(self, client, server):
        # pylint: disable=C0301
        url = f"https://{server['ip']}/redfish/v1/Chassis/chassis/Sensors/chassis_output_power"
        data = await HttpClient.get(client, url, self.auth)
        if not data:
            return
        value = data.get("Reading", 0)
        self.add_metric(
            self.metrics_dict["psu_output_power_watt"],
            server["ip"],
            server["location"],
            "psu_output_power_watt",
            value,
            [server["name"]],
        )

    async def collect_psu_health(self, client, server, seq):
        # pylint: disable=C0301
        url = f"https://{server['ip']}/redfish/v1/Chassis/chassis/Power/Oem/tsmc/PSU{seq}"
        value = await self._collect_health(client, url)
        self.add_metric(
            self.metrics_dict["powershelf_psu_fail"],
            server["ip"],
            server["location"],
            f"PSU_{seq}",
            value,
            [server["name"]],
        )

    async def collect_chassis_a_health(self, client, server):
        # pylint: disable=C0301
        url = f"https://{server['ip']}/redfish/v1/Chassis/chassis/Sensors/chassis_A_input_Voltage"
        value = await self._collect_health(client, url)
        self.add_metric(
            self.metrics_dict["powershelf_chassis_fail"],
            server["ip"],
            server["location"],
            "Chassis_A",
            value,
            [server["name"]],
        )

    async def collect_chassis_b_health(self, client, server):
        # pylint: disable=C0301
        url = f"https://{server['ip']}/redfish/v1/Chassis/chassis/Sensors/chassis_B_input_Voltage"
        value = await self._collect_health(client, url)
        self.add_metric(
            self.metrics_dict["powershelf_chassis_fail"],
            server["ip"],
            server["location"],
            "Chassis_B",
            value,
            [server["name"]],
        )

    async def _collect_health(self, client, url):
        data = await HttpClient.get(client, url, self.auth)
        if not data:
            value = 1
        else:
            health = data.get("Status", {}).get("Health")
            value = 0 if health == "OK" else 1
        return value
