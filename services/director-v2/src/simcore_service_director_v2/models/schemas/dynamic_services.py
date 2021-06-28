from enum import Enum, unique
from pathlib import Path
from typing import Optional

from models_library.basic_types import PortInt
from models_library.projects import ProjectID
from models_library.projects_nodes_io import NodeID
from models_library.services import DYNAMIC_SERVICE_KEY_RE, VERSION_RE
from pydantic import BaseModel, Field

from .constants import UserID


@unique
class ServiceState(str, Enum):
    PENDING = "pending"
    PULLING = "pulling"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class ServiceDetails(BaseModel):
    key: str = Field(
        ...,
        description="distinctive name for the node based on the docker registry path",
        regex=DYNAMIC_SERVICE_KEY_RE,
        examples=[
            "simcore/services/dynamic/3dviewer",
        ],
        alias="service_key",
    )
    version: str = Field(
        ...,
        description="semantic version number of the node",
        regex=VERSION_RE,
        examples=["1.0.0", "0.0.1"],
        alias="service_version",
    )

    user_id: UserID
    project_id: ProjectID
    node_uuid: NodeID = Field(..., alias="service_uuid")

    basepath: Path = Field(
        None,
        description="predefined path where the dynamic service should be served. If empty, the service shall use the root endpoint.",
        alias="service_basepath",
    )

    class Config:
        schema_extra = {
            "example": {
                "key": "simcore/services/dynamic/3dviewer",
                "version": "2.4.5",
                "user_id": 234,
                "project_id": "dd1d04d9-d704-4f7e-8f0f-1ca60cc771fe",
                "node_uuid": "75c7f3f4-18f9-4678-8610-54a2ade78eaa",
                "basepath": "/x/75c7f3f4-18f9-4678-8610-54a2ade78eaa",
            }
        }
        allow_population_by_field_name = True


@unique
class ServiceBootType(str, Enum):
    V0 = "V0"
    V2 = "V2"


class RunningDynamicServiceDetails(ServiceDetails):
    boot_type: ServiceBootType = Field(
        ...,
        description="describes how the dynamic services was started (legacy=V0, modern=V2)",
    )

    host: str = Field(
        ..., description="the service swarm internal host name", alias="service_host"
    )
    internal_port: PortInt = Field(
        ..., description="the service swarm internal port", alias="service_port"
    )
    published_port: PortInt = Field(
        None, description="the service swarm published port if any", deprecated=True
    )

    entry_point: Optional[str] = Field(
        None,
        description="if empty the service entrypoint is on the root endpoint.",
        deprecated=True,
    )
    state: ServiceState = Field(
        ..., description="service current state", alias="service_state"
    )
    message: Optional[str] = Field(
        None,
        description="additional information related to service state",
        alias="service_message",
    )

    @classmethod
    def from_monitoring_status(
        cls,
        node_uuid: NodeID,
        monitor_data: "MonitorData",
        service_state: ServiceState,
        service_message: str,
    ) -> "RunningDynamicServiceDetails":
        return cls(
            boot_type=ServiceBootType.V2,
            user_id=monitor_data.user_id,
            project_id=monitor_data.project_id,
            node_uuid=node_uuid,
            key=monitor_data.service_key,
            version=monitor_data.service_tag,
            host=monitor_data.service_name,
            internal_port=monitor_data.service_port,
            state=service_state.value,
            message=service_message,
        )

    class Config(ServiceDetails.Config):
        schema_extra = {
            "examples": [
                {
                    "boot_type": "V0",
                    "key": "simcore/services/dynamic/3dviewer",
                    "version": "2.4.5",
                    "user_id": 234,
                    "project_id": "dd1d04d9-d704-4f7e-8f0f-1ca60cc771fe",
                    "uuid": "75c7f3f4-18f9-4678-8610-54a2ade78eaa",
                    "basepath": "/x/75c7f3f4-18f9-4678-8610-54a2ade78eaa",
                    "host": "3dviewer_75c7f3f4-18f9-4678-8610-54a2ade78eaa",
                    "internal_port": 8888,
                    "state": "running",
                    "message": "",
                    "node_uuid": "75c7f3f4-18f9-4678-8610-54a2ade78eaa",
                },
                {
                    "boot_type": "V2",
                    "key": "simcore/services/dynamic/dy-static-file-viewer-dynamic-sidecar",
                    "version": "1.0.0",
                    "user_id": 234,
                    "project_id": "dd1d04d9-d704-4f7e-8f0f-1ca60cc771fe",
                    "uuid": "75c7f3f4-18f9-4678-8610-54a2ade78eaa",
                    "host": "dy-sidecar_75c7f3f4-18f9-4678-8610-54a2ade78eaa",
                    "internal_port": 80,
                    "state": "running",
                    "message": "",
                    "node_uuid": "75c7f3f4-18f9-4678-8610-54a2ade78eaa",
                },
            ]
        }