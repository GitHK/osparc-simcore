import logging
import json
from pathlib import Path
from typing import Any, Dict, List
from pprint import pformat

from aiohttp import web

from .config import ServiceSidecarSettings, get_settings
from .constants import (
    FIXED_SERVICE_NAME_PROXY,
    FIXED_SERVICE_NAME_SIDECAR,
    SERVICE_SIDECAR_PREFIX,
)
from .docker_utils import (
    create_network,
    create_service_and_get_id,
    inspect_service,
    get_swarm_network,
    get_node_id_from_task_for_service,
)
from .monitor import get_monitor
from .utils import unused_port
from .exceptions import ServiceSidecarError

log = logging.getLogger(__name__)


def strip_service_name(service_name: str) -> str:
    """returns: the maximum allowed service name in docker swarm"""
    return service_name[:63]


def assemble_service_name(
    project_id: str, service_key: str, node_uuid: str, fixed_service: str
) -> str:
    first_two_project_id = project_id[:2]
    name_from_service_key = service_key.split("/")[-1]
    return strip_service_name(
        f"{SERVICE_SIDECAR_PREFIX}_{node_uuid}_{first_two_project_id}"
        f"_{fixed_service}_{name_from_service_key}"
    )


async def get_service_sidecar_stack_status(
    app: web.Application, node_uuid: str
) -> Dict[str, Any]:
    monitor = get_monitor(app)
    return await monitor.get_stack_status(node_uuid)


async def stop_service_sidecar_stack_for_service(
    app: web.Application, node_uuid: str
) -> None:
    """will trigger actions needed to stop the service: removal from monitoring"""
    monitor = get_monitor(app)
    await monitor.remove_service_from_monitor(node_uuid)


def _extract_service_port_from_compose_start_spec(
    create_service_params: Dict[str, Any]
) -> int:
    return create_service_params["labels"]["service_port"]


async def start_service_sidecar_stack_for_service(  # pylint: disable=too-many-arguments
    app: web.Application,
    user_id: str,
    project_id: str,
    service_key: str,
    service_tag: str,
    node_uuid: str,
    settings: List[Dict[str, Any]],
) -> Dict[str, str]:
    debug_message = (
        f"SERVICE_SIDECAR: user_id={user_id}, project_id={project_id}, service_key={service_key}, "
        f"service_tag={service_tag}, node_uuid={node_uuid}"
    )
    # TODO: change the current interface , parameters will be ignored by this service
    # - internal_network_id
    # - node_base_path
    # - main_service

    log.debug(debug_message)

    service_sidecar_settings: ServiceSidecarSettings = get_settings(app)

    # Service naming schema:
    # -  srvsdcr_{uuid}_{first_two_project_id}_proxy_{name_from_service_key}
    # -  srvsdcr_{uuid}_{first_two_project_id}_sidecar_{name_from_service_key}

    service_name_service_sidecar = assemble_service_name(
        project_id, service_key, node_uuid, FIXED_SERVICE_NAME_SIDECAR
    )
    service_name_proxy = assemble_service_name(
        project_id, service_key, node_uuid, FIXED_SERVICE_NAME_PROXY
    )

    first_two_project_id = project_id[:2]

    # unique name for the traefik constraints
    io_simcore_zone = f"{SERVICE_SIDECAR_PREFIX}_{node_uuid}_{first_two_project_id}"

    # based on the node_id and project_id
    service_sidecar_network_name = (
        f"{SERVICE_SIDECAR_PREFIX}_{node_uuid}_{first_two_project_id}"
    )
    # these configuration should guarantee 245 address network
    network_config = {
        "Name": service_sidecar_network_name,
        "Driver": "overlay",
        "Labels": {
            "io.simcore.zone": f"{service_sidecar_settings.traefik_simcore_zone}",
            "com.simcore.description": f"interactive for node: {node_uuid}_{first_two_project_id}",
            "uuid": node_uuid,  # needed for removal when project is closed
        },
        "Attachable": True,
        "Internal": False,
    }
    service_sidecar_network_id = await create_network(network_config)

    # attach the service to the swarm network dedicated to services
    swarm_network = await get_swarm_network(service_sidecar_settings)
    swarm_network_id = swarm_network["Id"]
    swarm_network_name = swarm_network["Name"]

    # start service-sidecar and run the proxy on the same node

    service_sidecar_create_service_params = await _dyn_service_sidecar_assembly(
        service_sidecar_settings=service_sidecar_settings,
        io_simcore_zone=io_simcore_zone,
        service_sidecar_network_name=service_sidecar_network_name,
        service_sidecar_network_id=service_sidecar_network_id,
        swarm_network_id=swarm_network_id,
        service_sidecar_name=service_name_service_sidecar,
        user_id=user_id,
        node_uuid=node_uuid,
        service_key=service_key,
        service_tag=service_tag,
        project_id=project_id,
        settings=settings,
    )
    log.debug(
        "service-sidecar create_service_params %s",
        pformat(service_sidecar_create_service_params),
    )

    service_sidecar_id = await create_service_and_get_id(
        service_sidecar_create_service_params
    )

    service_sidecar_node_id = await get_node_id_from_task_for_service(
        service_sidecar_id, service_sidecar_settings
    )

    service_sidecar_proxy_create_service_params = await _dyn_proxy_entrypoint_assembly(
        service_sidecar_settings=service_sidecar_settings,
        node_uuid=node_uuid,
        io_simcore_zone=io_simcore_zone,
        service_sidecar_network_name=service_sidecar_network_name,
        service_sidecar_network_id=service_sidecar_network_id,
        service_name=service_name_proxy,
        swarm_network_id=swarm_network_id,
        swarm_network_name=swarm_network_name,
        user_id=user_id,
        project_id=project_id,
        service_sidecar_node_id=service_sidecar_node_id,
    )
    log.debug(
        "service-sidecar-proxy create_service_params %s",
        pformat(service_sidecar_proxy_create_service_params),
    )

    service_sidecar_proxy_id = await create_service_and_get_id(
        service_sidecar_proxy_create_service_params
    )

    # services where successfully started and they can be monitored
    monitor = get_monitor(app)
    await monitor.add_service_to_monitor(
        service_name=service_name_service_sidecar,
        node_uuid=node_uuid,
        hostname=service_name_service_sidecar,
        port=service_sidecar_settings.web_service_port,
        service_key=service_key,
        service_tag=service_tag,
        service_sidecar_network_name=service_sidecar_network_name,
        simcore_traefik_zone=io_simcore_zone,
        service_port=_extract_service_port_from_compose_start_spec(
            service_sidecar_create_service_params
        ),
    )

    # returning data for the proxy service so the service UI metadata can be extracted from here
    return await inspect_service(service_sidecar_proxy_id)


async def _dyn_proxy_entrypoint_assembly(  # pylint: disable=too-many-arguments
    service_sidecar_settings: ServiceSidecarSettings,
    node_uuid: str,
    io_simcore_zone: str,
    service_sidecar_network_name: str,
    service_sidecar_network_id: str,
    service_name: str,
    swarm_network_id: str,
    swarm_network_name: str,
    user_id: str,
    project_id: str,
    service_sidecar_node_id: str,
) -> Dict[str, Any]:
    """This is the entrypoint to the network and needs to be configured properly"""

    mounts = [
        # docker socket needed to use the docker api
        {
            "Source": "/var/run/docker.sock",
            "Target": "/var/run/docker.sock",
            "Type": "bind",
        }
    ]

    return {
        "labels": {
            "io.simcore.zone": f"{service_sidecar_settings.traefik_simcore_zone}",
            "swarm_stack_name": service_sidecar_settings.swarm_stack_name,
            "traefik.docker.network": swarm_network_name,
            "traefik.enable": "true",
            f"traefik.http.routers.{service_name}.entrypoints": "http",
            f"traefik.http.routers.{service_name}.middlewares": "master-simcore_gzip@docker",
            f"traefik.http.routers.{service_name}.priority": "10",
            # http://91d7e754-609f-4ab5-9d77-c441aa46c601.services.10.43.103.168.xip.io:9081
            # TODO: should the below url not be `{project_id}_{node_uuid}.services.{{host:.+}}` to avoid collisions?nea
            f"traefik.http.routers.{service_name}.rule": f"hostregexp(`{node_uuid}.services.{{host:.+}}`)",
            f"traefik.http.services.{service_name}.loadbalancer.server.port": "80",
            "type": "main",  # main is required to be monitored by the frontend
            "dynamic_type": "dynamic-sidecar",  # tagged as dynamic service
            "study_id": project_id,
            "user_id": user_id,
            "uuid": node_uuid,  # needed for removal when project is closed
        },
        "name": service_name,
        "networks": [swarm_network_id, service_sidecar_network_id],
        "task_template": {
            "ContainerSpec": {
                "Env": {},
                "Hosts": [],
                "Image": "traefik:v2.2.1",
                "Init": True,
                "Labels": {},
                "Command": [
                    "traefik",
                    "--log.level=DEBUG",
                    "--accesslog=true",
                    "--entryPoints.http.address=:80",
                    "--entryPoints.http.forwardedHeaders.insecure",
                    "--providers.docker.endpoint=unix:///var/run/docker.sock",
                    f"--providers.docker.network={service_sidecar_network_name}",
                    "--providers.docker.exposedByDefault=false",
                    f"--providers.docker.constraints=Label(`io.simcore.zone`, `{io_simcore_zone}`)",
                    # inject basic auth https://doc.traefik.io/traefik/v2.0/middlewares/basicauth/
                    # TODO: attach new auth_url to the service and make it available in the monitor
                ],
                "Mounts": mounts,
            },
            "Placement": {
                "Constraints": [
                    "node.platform.os == linux",  # TODO: ask SAN should this be removed?
                    f"node.id == {service_sidecar_node_id}",
                ]
            },
            # TODO: ask SAN how much resoruces for the proxy
            "Resources": {
                "Limits": {"MemoryBytes": 1073741824, "NanoCPUs": 2000000000},
                "Reservations": {"MemoryBytes": 524288000, "NanoCPUs": 100000000},
            },
            "RestartPolicy": {
                "Condition": "on-failure",
                "Delay": 5000000,
                "MaxAttempts": 2,
            },
        },
    }


def _check_setting_correctness(setting: Dict) -> None:
    if "name" not in setting or "type" not in setting or "value" not in setting:
        raise ServiceSidecarError("Invalid setting in %s" % setting)


def _parse_mount_settings(settings: List[Dict]) -> List[Dict]:
    mounts = list()
    for s in settings:
        log.debug("Retrieved mount settings %s", s)
        mount = dict()
        mount["ReadOnly"] = True
        if "ReadOnly" in s and s["ReadOnly"] in ["false", "False", False]:
            mount["ReadOnly"] = False

        for field in ["Source", "Target", "Type"]:
            if field in s:
                mount[field] = s[field]
            else:
                log.warning(
                    "Mount settings have wrong format. Required keys [Source, Target, Type]"
                )
                continue

        log.debug("Append mount settings %s", mount)
        mounts.append(mount)

    return mounts


def _parse_env_settings(settings: List[str]) -> Dict:
    envs = dict()
    for s in settings:
        log.debug("Retrieved env settings %s", s)
        if "=" in s:
            parts = s.split("=")
            if len(parts) == 2:
                envs.update({parts[0]: parts[1]})

        log.debug("Parsed env settings %s", s)

    return envs


# pylint: disable=too-many-branches
def _inject_settings_to_create_service_params(
    node_uuid: str,
    labels_service_settings: List[Dict[str, Any]],
    create_service_params: Dict[str, Any],
) -> None:
    for param in labels_service_settings:
        _check_setting_correctness(param)
        # replace %service_uuid% by the given uuid
        if str(param["value"]).find("%service_uuid%") != -1:
            dummy_string = json.dumps(param["value"])
            dummy_string = dummy_string.replace("%service_uuid%", node_uuid)
            param["value"] = json.loads(dummy_string)

        # NOTE: the below capitalize addresses a bug in a lot of already in use services
        # where Resources was written in lower case
        if param["type"].capitalize() == "Resources":
            # python-API compatible for backward compatibility
            if "mem_limit" in param["value"]:
                create_service_params["task_template"]["Resources"]["Limits"][
                    "MemoryBytes"
                ] = param["value"]["mem_limit"]
            if "cpu_limit" in param["value"]:
                create_service_params["task_template"]["Resources"]["Limits"][
                    "NanoCPUs"
                ] = param["value"]["cpu_limit"]
            if "mem_reservation" in param["value"]:
                create_service_params["task_template"]["Resources"]["Reservations"][
                    "MemoryBytes"
                ] = param["value"]["mem_reservation"]
            if "cpu_reservation" in param["value"]:
                create_service_params["task_template"]["Resources"]["Reservations"][
                    "NanoCPUs"
                ] = param["value"]["cpu_reservation"]
            # REST-API compatible
            if "Limits" in param["value"] or "Reservations" in param["value"]:
                create_service_params["task_template"]["Resources"].update(
                    param["value"]
                )

        # publishing port on the ingress network.
        elif param["name"] == "ports" and param["type"] == "int":  # backward comp
            create_service_params["labels"]["port"] = create_service_params["labels"][
                "service_port"
            ] = str(param["value"])
        # REST-API compatible
        elif param["type"] == "EndpointSpec":
            if "Ports" in param["value"]:
                if (
                    isinstance(param["value"]["Ports"], list)
                    and "TargetPort" in param["value"]["Ports"][0]
                ):
                    create_service_params["labels"]["port"] = create_service_params[
                        "labels"
                    ]["service_port"] = str(param["value"]["Ports"][0]["TargetPort"])

        # placement constraints
        elif param["name"] == "constraints":  # python-API compatible
            create_service_params["task_template"]["Placement"]["Constraints"] += param[
                "value"
            ]
        elif param["type"] == "Constraints":  # REST-API compatible
            create_service_params["task_template"]["Placement"]["Constraints"] += param[
                "value"
            ]
        elif param["name"] == "env":
            log.debug("Found env parameter %s", param["value"])
            env_settings = _parse_env_settings(param["value"])
            if env_settings:
                create_service_params["task_template"]["ContainerSpec"]["Env"].update(
                    env_settings
                )
        elif param["name"] == "mount":
            log.debug("Found mount parameter %s", param["value"])
            mount_settings: List[Dict] = _parse_mount_settings(param["value"])
            if mount_settings:
                create_service_params["task_template"]["ContainerSpec"][
                    "Mounts"
                ].extend(mount_settings)

    container_spec = create_service_params["task_template"]["ContainerSpec"]
    # set labels for CPU and Memory limits
    container_spec["Labels"]["nano_cpus_limit"] = str(
        create_service_params["task_template"]["Resources"]["Limits"]["NanoCPUs"]
    )
    container_spec["Labels"]["mem_limit"] = str(
        create_service_params["task_template"]["Resources"]["Limits"]["MemoryBytes"]
    )


async def _dyn_service_sidecar_assembly(  # pylint: disable=too-many-arguments
    service_sidecar_settings: ServiceSidecarSettings,
    io_simcore_zone: str,
    service_sidecar_network_name: str,
    service_sidecar_network_id: str,
    swarm_network_id: str,
    service_sidecar_name: str,
    user_id: str,
    node_uuid: str,
    service_key: str,
    service_tag: str,
    project_id: str,
    settings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """This service contains the service-sidecar which will spawn the dynamic service itself """
    mounts = [
        # docker socket needed to use the docker api
        {
            "Source": "/var/run/docker.sock",
            "Target": "/var/run/docker.sock",
            "Type": "bind",
        }
    ]

    endpint_spec = {}

    if service_sidecar_settings.is_dev_mode:
        service_sidecar_path = service_sidecar_settings.dev_simcore_service_sidecar_path
        if service_sidecar_path is None:
            log.error(
                "Could not mount the sources for the service-sidecar, please provide env var named %s",
                service_sidecar_settings.dev_simcore_service_sidecar_path.__name__,
            )
        else:
            mounts.append(
                {
                    "Source": str(service_sidecar_path),
                    "Target": "/devel/services/service-sidecar",
                    "Type": "bind",
                }
            )
            packages_pacth = (
                Path(service_sidecar_settings.dev_simcore_service_sidecar_path)
                / ".."
                / ".."
                / "packages"
            )
            mounts.append(
                {
                    "Source": str(packages_pacth),
                    "Target": "/devel/packages",
                    "Type": "bind",
                }
            )
        # expose this service on an empty port
        if service_sidecar_settings.dev_expose_service_sidecar:
            endpint_spec["Ports"] = [
                {
                    "Protocol": "tcp",
                    "PublishedPort": unused_port(),
                    "TargetPort": service_sidecar_settings.web_service_port,
                }
            ]

    # used for the container name to avoid collisions for started containers on the same node
    compose_namespace = f"{SERVICE_SIDECAR_PREFIX}_{project_id}_{node_uuid}"

    create_service_params = {
        # "auth": {"password": "adminadmin", "username": "admin"},   # maybe not needed together with registry
        "endpoint_spec": endpint_spec,
        "labels": {
            "io.simcore.zone": io_simcore_zone,
            "port": f"{service_sidecar_settings.web_service_port}",
            "study_id": project_id,
            "traefik.docker.network": service_sidecar_network_name,  # also used for monitoring
            "traefik.enable": "true",
            f"traefik.http.routers.{service_sidecar_name}.entrypoints": "http",
            f"traefik.http.routers.{service_sidecar_name}.priority": "10",
            f"traefik.http.routers.{service_sidecar_name}.rule": "PathPrefix(`/`)",
            f"traefik.http.services.{service_sidecar_name}.loadbalancer.server.port": f"{service_sidecar_settings.web_service_port}",
            "type": "dependency",
            "user_id": user_id,
            # the following are used for monitoring
            "uuid": node_uuid,  # also needed for removal when project is closed
            "swarm_stack_name": service_sidecar_settings.swarm_stack_name,
            "service_key": service_key,
            "service_tag": service_tag,
        },
        "name": service_sidecar_name,
        "networks": [swarm_network_id, service_sidecar_network_id],
        "task_template": {
            "ContainerSpec": {
                "Env": {
                    "SIMCORE_HOST_NAME": service_sidecar_name,
                    "SERVICE_SIDECAR_COMPOSE_NAMESPACE": compose_namespace,
                    "SERVICE_SIDECAR_DOCKER_COMPOSE_DOWN_TIMEOUT": service_sidecar_settings.service_sidecar_api_request_docker_compose_down_timeout,
                },
                "Hosts": [],
                "Image": service_sidecar_settings.image,
                "Init": True,
                "Labels": {},
                "Mounts": mounts,
            },
            "Placement": {"Constraints": []},
            "RestartPolicy": {
                "Condition": "on-failure",
                "Delay": 5000000,
                "MaxAttempts": 2,
            },
            # this will get overwritten
            "Resources": {
                "Limits": {"NanoCPUs": 2 * pow(10, 9), "MemoryBytes": 1 * pow(1024, 3)},
                "Reservations": {
                    "NanoCPUs": 1 * pow(10, 8),
                    "MemoryBytes": 500 * pow(1024, 2),
                },
            },
        },
    }

    _inject_settings_to_create_service_params(
        node_uuid=node_uuid,
        labels_service_settings=settings,
        create_service_params=create_service_params,
    )

    return create_service_params