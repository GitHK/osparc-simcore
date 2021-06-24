import datetime
import logging
from enum import Enum
from typing import List, Optional

from models_library.basic_regex import VERSION_RE
from models_library.projects import ProjectID
from models_library.projects_nodes import NodeID
from models_library.service_settings_labels import (
    ComposeSpecLabel,
    PathsMappingLabel,
    SimcoreServiceLabels,
)
from models_library.services import SERVICE_KEY_RE
from pydantic import BaseModel, Field, PositiveInt, validator

from ....models.domains.dynamic_services import DynamicServiceCreate
from ....models.schemas.constants import UserID
from ..docker_utils import ServiceLabelsStoredData
from .utils import AsyncResourceLock

logger = logging.getLogger()


class DynamicSidecarStatus(str, Enum):
    OK = "ok"  # running as expected
    FAILING = "failing"  # requests to the sidecar API are failing service should be cosnidered as unavailable


class OverallStatus(BaseModel):
    """Generated from data from docker container inspect API"""

    status: DynamicSidecarStatus = Field(..., description="status of the service")
    info: str = Field(..., description="additional information for the user")

    def _update(self, new_status: DynamicSidecarStatus, new_info: str) -> None:
        self.status = new_status
        self.info = new_info

    def update_ok_status(self, info: str) -> None:
        self._update(DynamicSidecarStatus.OK, info)

    def update_failing_status(self, info: str) -> None:
        self._update(DynamicSidecarStatus.FAILING, info)

    def __eq__(self, other: "OverallStatus") -> bool:
        return self.status == other.status and self.info == other.info

    @classmethod
    def make_initially_ok(cls) -> "OverallStatus":
        # the service is initially ok when started
        initial_state = cls(status=DynamicSidecarStatus.OK, info="")
        return initial_state


class DockerStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    RESTARTING = "restarting"
    REMOVING = "removing"
    EXITED = "exited"
    DEAD = "dead"


class DockerContainerInspect(BaseModel):
    status: DockerStatus = Field(
        ...,
        scription="status of the underlying container",
    )
    name: str = Field(..., description="docker name of the container")
    id: str = Field(..., description="docker id of the container")

    last_updated: datetime.datetime = Field(
        default_factory=datetime.datetime.utcnow,
        description="time of the update in UTC",
    )


class DynamicSidecar(BaseModel):
    overall_status: OverallStatus = Field(
        OverallStatus.make_initially_ok(),
        description="status of the service sidecar also with additional information",
    )

    hostname: str = Field(..., description="docker hostname for this service")

    port: PositiveInt = Field(8000, description="dynamic-sidecar port")

    is_available: bool = Field(
        False,
        scription=(
            "is True while the health check on the dynamic-sidecar is responding. "
            "Meaning that the dynamic-sidecar is reachable and can accept requests"
        ),
    )

    @property
    def compose_spec_submitted(self) -> bool:
        """
        If the director-v2 is rebooted was_compose_spec_submitted is False
        If the compose-spec is submitted it can be safely assumed that the
        containers_inspect contains some elements.
        """
        return self.was_compose_spec_submitted or len(self.containers_inspect)

    was_compose_spec_submitted: bool = Field(
        False,
        description="if the docker-compose spec was already submitted this fields is True",
    )

    containers_inspect: List[DockerContainerInspect] = Field(
        [],
        scription="docker inspect results from all the container ran at regular intervals",
    )

    were_services_created: bool = Field(
        False,
        description=(
            "when True no longer will the Docker api "
            "be used to check if the services were started"
        ),
    )

    @property
    def can_save_state(self) -> bool:
        """
        Keeps track of the current state of the application, if it was starte successfully
        the state of the service can be saved when stopping the service
        """
        # TODO: implement when adding save status hooks
        return False

    # consider adding containers for healthchecks but this is more difficult and it depends on each service

    @property
    def endpoint(self):
        """endpoint where all the services are exposed"""
        return f"http://{self.hostname}:{self.port}"

    @property
    def are_containers_ready(self) -> bool:
        """returns: True if all containers are in running state"""
        return all(
            docker_container_inspect.status == DockerStatus.RUNNING
            for docker_container_inspect in self.containers_inspect
        )


class MonitorData(BaseModel):
    service_name: str = Field(
        ..., description="Name of the current dynamic-sidecar being monitored"
    )

    node_uuid: NodeID = Field(
        ..., description="the node_id of the service as defined in the workbench"
    )

    project_id: ProjectID = Field(
        ..., description="project_uuid required by the status"
    )

    user_id: UserID = Field(..., description="user_id required by the status")

    dynamic_sidecar: DynamicSidecar = Field(
        ...,
        description="stores information fetched from the dynamic-sidecar",
    )

    service_key: str = Field(
        ...,
        regex=SERVICE_KEY_RE,
        description="together with the tag used to compose the docker-compose spec for the service",
    )
    service_tag: str = Field(
        ...,
        regex=VERSION_RE,
        description="together with the key used to compose the docker-compose spec for the service",
    )
    paths_mapping: PathsMappingLabel = Field(
        ...,
        description=(
            "the service explicitly requests where to mount all paths "
            "which will be handeled by the dynamic-sidecar"
        ),
    )
    compose_spec: ComposeSpecLabel = Field(
        ...,
        description=(
            "if the user provides a compose_spec, it will be used instead "
            "of compsing one from the service_key and service_tag"
        ),
    )
    container_http_entry: Optional[str] = Field(
        ...,
        description="when the user defines a compose spec, it should pick a container inside the spec to receive traffic on a defined port",
    )

    dynamic_sidecar_network_name: str = Field(
        ...,
        description="overlay network biding the proxy to the container spaned by the dynamic-sidecar",
    )

    simcore_traefik_zone: str = Field(
        ...,
        description="required for Traefik to correctly route requests to the spawned container",
    )

    service_port: PositiveInt = Field(
        ...,
        description=(
            "port where the service is exposed defined by the service; "
            "NOTE: optional because it will be added once the service is started"
        ),
    )
    # Below values are used only once and then are nto required, thus optional
    # after the service is picked up by the monitor after a reboot these are not required
    # and can be set to None
    request_dns: Optional[str] = Field(
        None, description="used when configuring the CORS options on the proxy"
    )
    request_scheme: Optional[str] = Field(
        None, description="used when configuring the CORS options on the proxy"
    )
    proxy_service_name: Optional[str] = Field(
        None, description="service name given to the proxy"
    )

    @validator("project_id", always=True)
    @classmethod
    def str_project_id(cls, v):
        return str(v)

    @classmethod
    def make_from_http_request(
        # pylint: disable=too-many-arguments
        cls,
        service_name: str,
        service: DynamicServiceCreate,
        simcore_service_labels: SimcoreServiceLabels,
        dynamic_sidecar_network_name: str,
        simcore_traefik_zone: str,
        service_port: int,
        hostname: str,
        port: Optional[int],
        request_dns: str = None,
        request_scheme: str = None,
        proxy_service_name: str = None,
    ) -> "MonitorData":
        return cls.parse_obj(
            dict(
                service_name=service_name,
                node_uuid=service.node_uuid,
                project_id=service.project_id,
                user_id=service.user_id,
                service_key=service.key,
                service_tag=service.version,
                paths_mapping=simcore_service_labels.paths_mapping,
                compose_spec=simcore_service_labels.compose_spec,
                container_http_entry=simcore_service_labels.container_http_entry,
                dynamic_sidecar_network_name=dynamic_sidecar_network_name,
                simcore_traefik_zone=simcore_traefik_zone,
                service_port=service_port,
                request_dns=request_dns,
                request_scheme=request_scheme,
                proxy_service_name=proxy_service_name,
                dynamic_sidecar=dict(
                    hostname=hostname,
                    port=port,
                ),
            )
        )

    @classmethod
    def make_from_service_labels_stored_data(
        cls,
        service_labels_stored_data: ServiceLabelsStoredData,
        port: Optional[int],
        request_dns: str = None,
        request_scheme: str = None,
        proxy_service_name: str = None,
    ) -> "MonitorData":
        return cls.parse_obj(
            dict(
                service_name=service_labels_stored_data.service_name,
                node_uuid=service_labels_stored_data.node_uuid,
                project_id=service_labels_stored_data.project_id,
                user_id=service_labels_stored_data.user_id,
                service_key=service_labels_stored_data.service_key,
                service_tag=service_labels_stored_data.service_tag,
                paths_mapping=service_labels_stored_data.paths_mapping,
                compose_spec=service_labels_stored_data.compose_spec,
                container_http_entry=service_labels_stored_data.container_http_entry,
                dynamic_sidecar_network_name=service_labels_stored_data.dynamic_sidecar_network_name,
                simcore_traefik_zone=service_labels_stored_data.simcore_traefik_zone,
                service_port=service_labels_stored_data.service_port,
                request_dns=request_dns,
                request_scheme=request_scheme,
                proxy_service_name=proxy_service_name,
                dynamic_sidecar=dict(
                    hostname=service_labels_stored_data.service_name,
                    port=port,
                ),
            )
        )


class LockWithMonitorData(BaseModel):
    # locking is required to guarantee the monitoring will not be triggered
    # twice in a row for the service
    resource_lock: AsyncResourceLock = Field(
        ...,
        description=(
            "needed to exclude the service from a monitoring cycle while another "
            "monitoring cycle is already running"
        ),
    )

    monitor_data: MonitorData = Field(
        ..., description="required data used to monitor the dynamic-sidecar"
    )

    class Config:
        arbitrary_types_allowed = True
