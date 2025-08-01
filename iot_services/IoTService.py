import datetime
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Dict

import numpy as np
from prometheus_client import start_http_server, Gauge

import utils
from RedisClient import RedisClient
from agent.es_registry import ESType, ESRegistry, ServiceID, ServiceType

logger = logging.getLogger("multiscale")

CONTAINER_REF = utils.get_env_param("CONTAINER_REF", "Unknown")
CONTAINER_IP = utils.get_env_param("CONTAINER_IP", "Unknown")
REDIS_INSTANCE = utils.get_env_param("REDIS_INSTANCE", "localhost")


class IoTService(ABC):
    def __init__(self, store_to_csv=True):
        self.docker_container_ref = CONTAINER_REF
        self.service_type = ServiceType.UNKNOWN
        self._terminated = True
        self._running = False
        self.service_conf = {}
        self.cores_reserved: float = 2.0
        self.es_registry = ESRegistry("./config/es_registry.json")
        self.store_to_csv = store_to_csv

        self.simulate_arrival_interval = True
        self.processing_timeframe = 1000  # ms
        self.client_arrivals: Dict[str, int] = {}

        self.redis_client = RedisClient(host=REDIS_INSTANCE)
        # self.docker_client = DockerClient()
        self.container_ip = CONTAINER_IP  # self.docker_client.get_container_ip(self.docker_container_ref)
        self.flag_metric_cooldown = 0

        start_http_server(8000)  # Last time I tried to get rid of the metric_id I had problems when querying the data
        self.prom_throughput = Gauge('throughput', 'Actual throughput', ['service_type', 'container_id', 'metric_id'])
        self.prom_avg_p_latency = Gauge('avg_p_latency', 'Processing latency / item', ['service_type', 'container_id', 'metric_id'])
        self.prom_quality = Gauge('data_quality', 'Current configured quality', ['service_type', 'container_id', 'metric_id'])
        self.prom_cores = Gauge('cores', 'Current configured cores', ['service_type', 'container_id', 'metric_id'])
        self.prom_model_size = Gauge('model_size', 'Current model size', ['service_type', 'container_id', 'metric_id'])

    def export_processing_metrics(self, processed_item_counter, processed_item_durations):
        # This is only executed once after the batch is processed
        self.prom_throughput.labels(container_id=self.docker_container_ref, service_type=self.service_type.value,
                                    metric_id="throughput").set(processed_item_counter)
        avg_p_latency_v = int(np.mean(processed_item_durations)) if processed_item_counter > 0 else -1
        self.prom_avg_p_latency.labels(container_id=self.docker_container_ref, service_type=self.service_type.value,
                                       metric_id="avg_p_latency").set(avg_p_latency_v)
        self.prom_cores.labels(container_id=self.docker_container_ref, service_type=self.service_type.value,
                               metric_id="cores").set(self.cores_reserved)
        self.prom_quality.labels(container_id=self.docker_container_ref, service_type=self.service_type.value,
                                 metric_id="data_quality").set(self.service_conf['data_quality'])

        if self.service_type == ServiceType.CV:
            self.prom_model_size.labels(container_id=self.docker_container_ref, service_type=self.service_type.value,
                                        metric_id="model_size").set(self.service_conf['model_size'])


        if self.store_to_csv:
            metric_buffer = [(datetime.datetime.now(), self.service_type.value, CONTAINER_REF, avg_p_latency_v,
                              self.service_conf, self.cores_reserved, utils.to_absolut_rps(self.client_arrivals),
                              processed_item_counter, self.flag_metric_cooldown)]
            self.flag_metric_cooldown = 0
            utils.write_metrics_to_csv(metric_buffer)
            metric_buffer.clear()

    @abstractmethod
    def process_one_iteration(self, frame) -> None:
        pass

    def start_process(self):
        self._terminated = False
        self._running = True

        processing_thread = threading.Thread(target=self.process_loop, daemon=True)
        processing_thread.start()
        logger.info(f"{self.service_type} started with {self.service_conf}")

    def terminate(self):
        self._running = False

    def is_running(self):
        return self._running

    @abstractmethod
    def process_loop(self):
        pass

    def change_config(self, config):
        self.service_conf = config
        logger.info(f"{self.service_type} changed to {config}")

    def vertical_scaling(self, c_cores: float):
        self.cores_reserved = c_cores
        logger.info(f"{self.service_type} set to {c_cores} cores")
        self.set_flag_and_cooldown(ESType.RESOURCE_SCALE)

    def change_request_arrival(self, client_id: str, client_rps: int):
        if client_rps <= 0:
            self.client_arrivals[client_id] = 0  # Should be able to delete this??
            del self.client_arrivals[client_id]
            logger.info(f"Removed client {client_id} from service {self.service_type}")
        else:
            self.client_arrivals[client_id] = client_rps
            logger.info(f"Client {client_id} changed RPS to {client_rps}")

        self.redis_client.store_assignment(self.get_service_id(), self.client_arrivals)
        logger.info(f"Total RPS is now {utils.to_absolut_rps(self.client_arrivals)}")

    def has_processing_timeout(self, start_time):
        time_elapsed = int((time.perf_counter() - start_time) * 1000)
        return time_elapsed >= self.processing_timeframe

    def simulate_interval(self, start_time):
        time_elapsed = int((time.perf_counter() - start_time) * 1000)
        if time_elapsed < self.processing_timeframe:
            time.sleep((self.processing_timeframe - time_elapsed) / 1000)

    def get_service_id(self):
        return ServiceID(self.container_ip, self.service_type, self.docker_container_ref)

    def set_flag_and_cooldown(self, es_type: ESType):
        self.flag_metric_cooldown = self.es_registry.get_es_cooldown(self.service_type, es_type)
        self.redis_client.store_cooldown(self.get_service_id(), es_type, self.flag_metric_cooldown)
