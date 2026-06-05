"""Server Collector"""

import httpx
from prometheus_client.core import GaugeMetricFamily

from tsre.core.logger.log import get_logger
from src.collectors.base import BaseCollector
from src.utils.http_client import HttpClient
from config.setting import Settings

logger = get_logger("exporter_logger")
setting = Settings()


class ServerCollector(BaseCollector):
    def __init__(self) -> None:
        super().__init__()
        self.server_list = setting.server_list
        self.auth = (setting.server_account, setting.server_pwd)
        labels = ["sensor_name", "server_name", "rack_name"]
        self.metrics_dict = {
            "server_cpu_temperature_celsius": GaugeMetricFamily(
                setting.metric_prefix + "server_cpu_temperature_celsius",
                "CPU temperature sensors",
                labels=labels,
            ),
            "server_memory_temperature_celsius": GaugeMetricFamily(
                setting.metric_prefix + "server_memory_temperature_celsius",
                "Memory temperature sensors",
                labels=labels,
            ),
            "server_gpu_temperature_celsius": GaugeMetricFamily(
                setting.metric_prefix + "server_gpu_temperature_celsius",
                "GPU temperature sensors",
                labels=labels,
            ),
            "server_power_watt": GaugeMetricFamily(
                setting.metric_prefix + "server_power_watt",
                "Total power reading",
                labels=labels,
            ),
            "server_fan_power_watt": GaugeMetricFamily(
                setting.metric_prefix + "server_fan_power_watt",
                "Total fan power reading",
                labels=labels,
            ),
            "server_cpu_power_watt": GaugeMetricFamily(
                setting.metric_prefix + "server_cpu_power_watt",
                "Total CPU power reading",
                labels=labels,
            ),
            "server_gpu_power_watt": GaugeMetricFamily(
                setting.metric_prefix + "server_gpu_power_watt",
                "Total GPU power reading",
                labels=labels,
            ),
            "server_mem_power_watt": GaugeMetricFamily(
                setting.metric_prefix + "server_mem_power_watt",
                "Total memory power reading",
                labels=labels,
            ),
        }

        self.sensor_gauge_map = {
            "Temp_CPU0": self.metrics_dict["server_cpu_temperature_celsius"],
            "Temp_CPU1": self.metrics_dict["server_cpu_temperature_celsius"],
            "Temp_CPU0_DIMMG0": self.metrics_dict[
                "server_memory_temperature_celsius"
            ],
            "Temp_CPU0_DIMMG1": self.metrics_dict[
                "server_memory_temperature_celsius"
            ],
            "Temp_CPU1_DIMMG0": self.metrics_dict[
                "server_memory_temperature_celsius"
            ],
            "Temp_CPU1_DIMMG1": self.metrics_dict[
                "server_memory_temperature_celsius"
            ],
            "Temp_GPU_1": self.metrics_dict["server_gpu_temperature_celsius"],
            "Temp_GPU_2": self.metrics_dict["server_gpu_temperature_celsius"],
            "Temp_GPU_3": self.metrics_dict["server_gpu_temperature_celsius"],
            "Temp_GPU_4": self.metrics_dict["server_gpu_temperature_celsius"],
        }
        self.power_gauge_map = {
            "Pwr_Node_Total": self.metrics_dict["server_power_watt"],
            "Pwr_Fan_Total": self.metrics_dict["server_fan_power_watt"],
            "Pwr_CPU_Total": self.metrics_dict["server_cpu_power_watt"],
            "Pwr_GPU_Total": self.metrics_dict["server_gpu_power_watt"],
            "Pwr_Mem_Total": self.metrics_dict["server_mem_power_watt"],
        }

    async def collect_metrics(
        self, client: httpx.AsyncClient, semaphore, server, _seq
    ):
        async with semaphore:
            try:
                await self.collect_thermal(client, server)
                await self.collect_node_power(client, server)
                await self.collect_fan_power(client, server)
                await self.collect_cpu_power(client, server)
                await self.collect_gpu_power(client, server)
                await self.collect_dimm_power(client, server)
            except Exception as e:
                logger.error(f"{server['location']}: {e}")

    # def collect_power_state(self, session, server):
    #     url = f"https://{server['ip']}/redfish/v1/Systems/Self"
    #     data = HttpClient.get(session, url, self.auth)
    #     if not data:
    #         return
    # logger.info(result)

    async def collect_thermal(self, client, server):
        url = f"https://{server['ip']}/redfish/v1/Chassis/Self/Thermal"
        data = await HttpClient.get(client, url, self.auth)
        if not data:
            return
        for item in data.get("Temperatures", []):
            sensor_name = item.get("Name", "Unknown")
            value = item.get("ReadingCelsius", 0)

            if sensor_name not in self.sensor_gauge_map:
                continue

            self.add_metric(
                self.sensor_gauge_map[sensor_name],
                server["ip"],
                server["location"],
                sensor_name,
                value,
            )

    async def collect_node_power(self, client, server):
        await self.collect_power(client, server, "Pwr_Node_Total")

    async def collect_fan_power(self, client, server):
        await self.collect_power(client, server, "Pwr_Fan_Total")

    async def collect_cpu_power(self, client, server):
        await self.collect_power(client, server, "Pwr_CPU_Total")

    async def collect_gpu_power(self, client, server):
        await self.collect_power(client, server, "Pwr_GPU_Total")

    async def collect_dimm_power(self, client, server):
        await self.collect_power(client, server, "Pwr_Mem_Total")

    async def collect_power(self, client, server, sensor_name):
        # pylint: disable=C0301
        url = f"https://{server['ip']}/redfish/v1/Chassis/Self/Sensors/{sensor_name}"
        data = await HttpClient.get(client, url, self.auth)
        if not data:
            return

        power = data.get("Reading", 0)
        self.add_metric(
            self.power_gauge_map[sensor_name],
            server["ip"],
            server["location"],
            sensor_name,
            power,
        )
