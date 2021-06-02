# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name
# pylint:disable=protected-access
# pylint:disable=no-value-for-parameter
# pylint:disable=too-many-arguments

import asyncio
import json
from collections import namedtuple
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, List
from uuid import UUID

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from models_library.projects import ProjectAtDB
from models_library.projects_nodes import NodeState
from models_library.projects_nodes_io import NodeID
from models_library.projects_pipeline import PipelineDetails
from models_library.projects_state import RunningState
from models_library.settings.rabbit import RabbitConfig
from models_library.settings.redis import RedisConfig
from pydantic.networks import AnyHttpUrl
from pydantic.types import PositiveInt
from simcore_postgres_database.models.comp_tasks import comp_tasks
from simcore_postgres_database.models.projects import projects
from simcore_service_director_v2.models.schemas.comp_tasks import ComputationTaskOut
from starlette import status
from starlette.responses import Response
from starlette.testclient import TestClient
from tenacity import retry, retry_if_exception_type, stop_after_delay, wait_random
from yarl import URL

pytest_simcore_core_services_selection = [
    "director",
    "redis",
    "rabbit",
    "sidecar",
    "storage",
    "postgres",
]
pytest_simcore_ops_services_selection = ["minio", "adminer"]

COMPUTATION_URL: str = "v2/computations"

# HELPERS ---------------------------------------


def _assert_pipeline_status(
    client: TestClient,
    url: AnyHttpUrl,
    user_id: PositiveInt,
    project_uuid: UUID,
    wait_for_states: List[RunningState] = None,
) -> ComputationTaskOut:
    if not wait_for_states:
        wait_for_states = [
            RunningState.SUCCESS,
            RunningState.FAILED,
            RunningState.ABORTED,
        ]

    MAX_TIMEOUT_S = 60

    @retry(
        stop=stop_after_delay(MAX_TIMEOUT_S),
        wait=wait_random(0, 2),
        retry=retry_if_exception_type(AssertionError),
        reraise=True,
    )
    def check_pipeline_state() -> ComputationTaskOut:
        response = client.get(url, params={"user_id": user_id})
        assert (
            response.status_code == status.HTTP_202_ACCEPTED
        ), f"response code is {response.status_code}, error: {response.text}"
        task_out = ComputationTaskOut.parse_obj(response.json())
        assert task_out.id == project_uuid
        assert task_out.url == f"{client.base_url}/v2/computations/{project_uuid}"
        print("Pipeline is in ", task_out.state)
        assert task_out.state in wait_for_states
        return task_out

    task_out = check_pipeline_state()

    return task_out


def _create_pipeline(
    client: TestClient,
    *,
    project: ProjectAtDB,
    user_id: PositiveInt,
    start_pipeline: bool,
    expected_response_status_code: status,
    **kwargs,
) -> Response:
    response = client.post(
        COMPUTATION_URL,
        json={
            "user_id": user_id,
            "project_id": str(project.uuid),
            "start_pipeline": start_pipeline,
            **kwargs,
        },
    )
    assert (
        response.status_code == expected_response_status_code
    ), f"response code is {response.status_code}, error: {response.text}"
    return response


def _assert_computation_task_out_obj(
    client: TestClient,
    task_out: ComputationTaskOut,
    *,
    project: ProjectAtDB,
    exp_task_state: RunningState,
    exp_pipeline_details: PipelineDetails,
):
    assert task_out.id == project.uuid
    assert task_out.state == exp_task_state
    assert task_out.url == f"{client.base_url}/v2/computations/{project.uuid}"
    assert task_out.stop_url == (
        f"{client.base_url}/v2/computations/{project.uuid}:stop"
        if exp_task_state in [RunningState.PUBLISHED, RunningState.PENDING]
        else None
    )
    # check pipeline details contents
    assert task_out.pipeline_details == exp_pipeline_details


# FIXTURES ---------------------------------------


@pytest.fixture(autouse=True)
def minimal_configuration(
    sleeper_service: Dict[str, str],
    jupyter_service: Dict[str, str],
    redis_service: RedisConfig,
    postgres_db: sa.engine.Engine,
    postgres_host_config: Dict[str, str],
    rabbit_service: RabbitConfig,
    simcore_services: Dict[str, URL],
    monkeypatch,
):
    pass


@pytest.fixture
def fake_workbench_without_outputs(
    fake_workbench_as_dict: Dict[str, Any]
) -> Dict[str, Any]:
    workbench = deepcopy(fake_workbench_as_dict)
    # remove all the outputs from the workbench
    for _, data in workbench.items():
        data["outputs"] = {}

    return workbench


@pytest.fixture
def update_project_workbench_with_comp_tasks(postgres_db: sa.engine.Engine) -> Callable:
    def updator(project_uuid: str):
        with postgres_db.connect() as con:
            result = con.execute(
                projects.select().where(projects.c.uuid == project_uuid)
            )
            prj_row = result.first()
            prj_workbench = prj_row.workbench

            result = con.execute(
                comp_tasks.select().where(comp_tasks.c.project_id == project_uuid)
            )
            # let's get the results and run_hash
            for task_row in result:
                # pass these to the project workbench
                prj_workbench[task_row.node_id]["outputs"] = task_row.outputs
                prj_workbench[task_row.node_id]["runHash"] = task_row.run_hash

            con.execute(
                projects.update()
                .values(workbench=prj_workbench)
                .where(projects.c.uuid == project_uuid)
            )

    yield updator


@pytest.fixture(scope="session")
def fake_workbench_node_states_file(mocks_dir: Path) -> Path:
    file_path = mocks_dir / "fake_workbench_computational_node_states.json"
    assert file_path.exists()
    return file_path


@pytest.fixture(scope="session")
def fake_workbench_computational_pipeline_details(
    fake_workbench_computational_adjacency_file: Path,
    fake_workbench_node_states_file: Path,
) -> PipelineDetails:
    adjacency_list = json.loads(fake_workbench_computational_adjacency_file.read_text())
    node_states = json.loads(fake_workbench_node_states_file.read_text())
    return PipelineDetails.parse_obj(
        {"adjacency_list": adjacency_list, "node_states": node_states}
    )


@pytest.fixture(scope="session")
def fake_workbench_computational_pipeline_details_completed(
    fake_workbench_computational_pipeline_details: PipelineDetails,
) -> PipelineDetails:
    completed_pipeline_details = deepcopy(fake_workbench_computational_pipeline_details)
    for node_state in completed_pipeline_details.node_states.values():
        node_state.modified = False
        node_state.dependencies = set()
        node_state.current_status = RunningState.SUCCESS
    return completed_pipeline_details


@pytest.fixture(scope="session")
def fake_workbench_computational_pipeline_details_not_started(
    fake_workbench_computational_pipeline_details: PipelineDetails,
) -> PipelineDetails:
    completed_pipeline_details = deepcopy(fake_workbench_computational_pipeline_details)
    for node_state in completed_pipeline_details.node_states.values():
        node_state.modified = True
        node_state.current_status = RunningState.NOT_STARTED
    return completed_pipeline_details


# TESTS ---------------------------------------


@pytest.mark.parametrize(
    "body,exp_response",
    [
        (
            {"user_id": "some invalid id", "project_id": "not a uuid"},
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ),
        (
            {"user_id": 2, "project_id": "not a uuid"},
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ),
        (
            {"user_id": 3, "project_id": "16e60a5d-834e-4267-b44d-3af49171bf21"},
            status.HTTP_404_NOT_FOUND,
        ),
    ],
)
def test_invalid_computation(client: TestClient, body: Dict, exp_response: int):
    # create a bunch of invalid stuff
    response = client.post(
        COMPUTATION_URL,
        json=body,
    )
    assert (
        response.status_code == exp_response
    ), f"response code is {response.status_code}, error: {response.text}"


def test_start_empty_computation(
    client: TestClient,
    user_id: PositiveInt,
    project: Callable,
):
    # send an empty project to process
    empty_project = project()
    _create_pipeline(
        client,
        project=empty_project,
        user_id=user_id,
        start_pipeline=True,
        expected_response_status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


PartialComputationParams = namedtuple(
    "PartialComputationParams",
    "subgraph_elements, exp_pipeline_adj_list, exp_node_states, exp_node_states_after_run",
)


@pytest.mark.parametrize(
    "subgraph_elements,exp_pipeline_adj_list, exp_node_states, exp_node_states_after_run",
    [
        pytest.param(
            *PartialComputationParams(
                subgraph_elements=[0, 1],
                exp_pipeline_adj_list={1: []},
                exp_node_states={
                    1: {
                        "modified": True,
                        "dependencies": [],
                        "currentStatus": RunningState.PUBLISHED,
                    },
                    2: {
                        "modified": True,
                        "dependencies": [1],
                    },
                    3: {
                        "modified": True,
                        "dependencies": [],
                    },
                    4: {
                        "modified": True,
                        "dependencies": [2, 3],
                    },
                },
                exp_node_states_after_run={
                    1: {
                        "modified": False,
                        "dependencies": [],
                        "currentStatus": RunningState.SUCCESS,
                    },
                    2: {
                        "modified": True,
                        "dependencies": [],
                    },
                    3: {
                        "modified": True,
                        "dependencies": [],
                    },
                    4: {
                        "modified": True,
                        "dependencies": [2, 3],
                    },
                },
            ),
            id="element 0,1",
        ),
        pytest.param(
            *PartialComputationParams(
                subgraph_elements=[1, 2, 4],
                exp_pipeline_adj_list={1: [2], 2: [4], 3: [4], 4: []},
                exp_node_states={
                    1: {
                        "modified": True,
                        "dependencies": [],
                        "currentStatus": RunningState.PUBLISHED,
                    },
                    2: {
                        "modified": True,
                        "dependencies": [1],
                        "currentStatus": RunningState.PUBLISHED,
                    },
                    3: {
                        "modified": True,
                        "dependencies": [],
                        "currentStatus": RunningState.PUBLISHED,
                    },
                    4: {
                        "modified": True,
                        "dependencies": [2, 3],
                        "currentStatus": RunningState.PUBLISHED,
                    },
                },
                exp_node_states_after_run={
                    1: {
                        "modified": False,
                        "dependencies": [],
                        "currentStatus": RunningState.SUCCESS,
                    },
                    2: {
                        "modified": False,
                        "dependencies": [],
                        "currentStatus": RunningState.SUCCESS,
                    },
                    3: {
                        "modified": False,
                        "dependencies": [],
                        "currentStatus": RunningState.SUCCESS,
                    },
                    4: {
                        "modified": False,
                        "dependencies": [],
                        "currentStatus": RunningState.SUCCESS,
                    },
                },
            ),
            id="element 1,2,4",
        ),
    ],
)
def test_run_partial_computation(
    client: TestClient,
    user_id: PositiveInt,
    project: Callable,
    update_project_workbench_with_comp_tasks: Callable,
    fake_workbench_without_outputs: Dict[str, Any],
    subgraph_elements: List[int],
    exp_pipeline_adj_list: Dict[int, List[str]],
    exp_node_states: Dict[int, Dict[str, Any]],
    exp_node_states_after_run: Dict[int, Dict[str, Any]],
):
    sleepers_project: ProjectAtDB = project(workbench=fake_workbench_without_outputs)

    def _convert_to_pipeline_details(
        project: ProjectAtDB,
        exp_pipeline_adj_list: Dict[int, List[str]],
        exp_node_states: Dict[int, Dict[str, Any]],
    ) -> PipelineDetails:
        workbench_node_uuids = list(project.workbench.keys())
        converted_adj_list: Dict[NodeID, Dict[NodeID, List[NodeID]]] = {}
        for node_key, next_nodes in exp_pipeline_adj_list.items():
            converted_adj_list[NodeID(workbench_node_uuids[node_key])] = [
                NodeID(workbench_node_uuids[n]) for n in next_nodes
            ]
        converted_node_states: Dict[NodeID, NodeState] = {
            NodeID(workbench_node_uuids[n]): NodeState(
                modified=s["modified"],
                dependencies={
                    workbench_node_uuids[dep_n] for dep_n in s["dependencies"]
                },
                currentStatus=s.get("currentStatus", RunningState.NOT_STARTED),
            )
            for n, s in exp_node_states.items()
        }
        return PipelineDetails(
            adjacency_list=converted_adj_list, node_states=converted_node_states
        )

    # convert the ids to the node uuids from the project
    expected_pipeline_details = _convert_to_pipeline_details(
        sleepers_project, exp_pipeline_adj_list, exp_node_states
    )

    # send a valid project with sleepers
    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=True,
        expected_response_status_code=status.HTTP_201_CREATED,
        subgraph=[
            str(node_id)
            for index, node_id in enumerate(sleepers_project.workbench)
            if index in subgraph_elements
        ],
    )
    task_out = ComputationTaskOut.parse_obj(response.json())
    # check the contents is correctb
    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.PUBLISHED,
        exp_pipeline_details=expected_pipeline_details,
    )

    # now wait for the computation to finish
    task_out = _assert_pipeline_status(
        client, task_out.url, user_id, sleepers_project.uuid
    )
    expected_pipeline_details_after_run = _convert_to_pipeline_details(
        sleepers_project, exp_pipeline_adj_list, exp_node_states_after_run
    )
    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.SUCCESS,
        exp_pipeline_details=expected_pipeline_details_after_run,
    )

    # run it a second time. the tasks are all up-to-date, nothing should be run
    # FIXME: currently the webserver is the one updating the projects table so we need to fake this by copying the run_hash
    update_project_workbench_with_comp_tasks(str(sleepers_project.uuid))

    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=True,
        expected_response_status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        subgraph=[
            str(node_id)
            for index, node_id in enumerate(sleepers_project.workbench)
            if index in subgraph_elements
        ],
    )

    # force run it this time.
    # the task are up-to-date but we force run them
    expected_pipeline_details_forced = deepcopy(expected_pipeline_details_after_run)
    for node_id, node_data in expected_pipeline_details_forced.node_states.items():
        node_data.current_status = expected_pipeline_details.node_states[
            node_id
        ].current_status
    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=True,
        expected_response_status_code=status.HTTP_201_CREATED,
        subgraph=[
            str(node_id)
            for index, node_id in enumerate(sleepers_project.workbench)
            if index in subgraph_elements
        ],
        force_restart=True,
    )
    task_out = ComputationTaskOut.parse_obj(response.json())

    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.PUBLISHED,
        exp_pipeline_details=expected_pipeline_details_forced,
    )

    # now wait for the computation to finish
    task_out = _assert_pipeline_status(
        client, task_out.url, user_id, sleepers_project.uuid
    )


def test_run_computation(
    client: TestClient,
    user_id: PositiveInt,
    project: Callable,
    fake_workbench_without_outputs: Dict[str, Any],
    update_project_workbench_with_comp_tasks: Callable,
    fake_workbench_computational_pipeline_details: PipelineDetails,
    fake_workbench_computational_pipeline_details_completed: PipelineDetails,
):
    sleepers_project = project(workbench=fake_workbench_without_outputs)
    # send a valid project with sleepers
    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=True,
        expected_response_status_code=status.HTTP_201_CREATED,
    )
    task_out = ComputationTaskOut.parse_obj(response.json())

    # check the contents is correct: a pipeline that just started gets PUBLISHED
    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.PUBLISHED,
        exp_pipeline_details=fake_workbench_computational_pipeline_details,
    )

    # wait for the computation to start
    _assert_pipeline_status(
        client,
        task_out.url,
        user_id,
        sleepers_project.uuid,
        wait_for_states=[RunningState.STARTED],
    )

    # wait for the computation to finish (either by failing, success or abort)
    task_out = _assert_pipeline_status(
        client, task_out.url, user_id, sleepers_project.uuid
    )

    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.SUCCESS,
        exp_pipeline_details=fake_workbench_computational_pipeline_details_completed,
    )

    # FIXME: currently the webserver is the one updating the projects table so we need to fake this by copying the run_hash
    update_project_workbench_with_comp_tasks(str(sleepers_project.uuid))
    # run again should return a 422 cause everything is uptodate
    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=True,
        expected_response_status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )

    # now force run again
    # the task are up-to-date but we force run them
    expected_pipeline_details_forced = deepcopy(
        fake_workbench_computational_pipeline_details_completed
    )
    for node_id, node_data in expected_pipeline_details_forced.node_states.items():
        node_data.current_status = (
            fake_workbench_computational_pipeline_details.node_states[
                node_id
            ].current_status
        )
    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=True,
        expected_response_status_code=status.HTTP_201_CREATED,
        force_restart=True,
    )
    task_out = ComputationTaskOut.parse_obj(response.json())
    # check the contents is correct
    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.PUBLISHED,
        exp_pipeline_details=expected_pipeline_details_forced,  # NOTE: here the pipeline already ran so its states are different
    )

    # wait for the computation to finish
    task_out = _assert_pipeline_status(
        client, task_out.url, user_id, sleepers_project.uuid
    )
    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.SUCCESS,
        exp_pipeline_details=fake_workbench_computational_pipeline_details_completed,
    )


def test_abort_computation(
    client: TestClient,
    user_id: PositiveInt,
    project: Callable,
    fake_workbench_without_outputs: Dict[str, Any],
    fake_workbench_computational_pipeline_details: PipelineDetails,
):
    sleepers_project = project(workbench=fake_workbench_without_outputs)
    # send a valid project with sleepers
    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=True,
        expected_response_status_code=status.HTTP_201_CREATED,
    )
    task_out = ComputationTaskOut.parse_obj(response.json())

    # check the contents is correctb
    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.PUBLISHED,
        exp_pipeline_details=fake_workbench_computational_pipeline_details,
    )

    # wait until the pipeline is started
    task_out = _assert_pipeline_status(
        client,
        task_out.url,
        user_id,
        sleepers_project.uuid,
        wait_for_states=[RunningState.STARTED],
    )
    assert (
        task_out.state == RunningState.STARTED
    ), f"pipeline is not in the expected starting state but in {task_out.state}"
    assert task_out.url == f"{client.base_url}/v2/computations/{sleepers_project.uuid}"
    assert (
        task_out.stop_url
        == f"{client.base_url}/v2/computations/{sleepers_project.uuid}:stop"
    )

    # now abort the pipeline
    response = client.post(f"{task_out.stop_url}", json={"user_id": user_id})
    assert (
        response.status_code == status.HTTP_202_ACCEPTED
    ), f"response code is {response.status_code}, error: {response.text}"
    task_out = ComputationTaskOut.parse_obj(response.json())
    assert (
        str(task_out.url)
        == f"{client.base_url}/v2/computations/{sleepers_project.uuid}"
    )
    assert task_out.stop_url == None

    # check that the pipeline is aborted/stopped
    task_out = _assert_pipeline_status(
        client,
        task_out.url,
        user_id,
        sleepers_project.uuid,
        wait_for_states=[RunningState.ABORTED],
    )
    assert task_out.state == RunningState.ABORTED


def test_update_and_delete_computation(
    client: TestClient,
    user_id: PositiveInt,
    project: Callable,
    fake_workbench_without_outputs: Dict[str, Any],
    fake_workbench_computational_pipeline_details_not_started: PipelineDetails,
    fake_workbench_computational_pipeline_details: PipelineDetails,
):
    sleepers_project = project(workbench=fake_workbench_without_outputs)
    # send a valid project with sleepers
    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=False,
        expected_response_status_code=status.HTTP_201_CREATED,
    )
    task_out = ComputationTaskOut.parse_obj(response.json())

    # check the contents is correctb
    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.NOT_STARTED,
        exp_pipeline_details=fake_workbench_computational_pipeline_details_not_started,
    )

    # update the pipeline
    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=False,
        expected_response_status_code=status.HTTP_201_CREATED,
    )
    task_out = ComputationTaskOut.parse_obj(response.json())

    # check the contents is correctb
    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.NOT_STARTED,
        exp_pipeline_details=fake_workbench_computational_pipeline_details_not_started,
    )

    # update the pipeline
    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=False,
        expected_response_status_code=status.HTTP_201_CREATED,
    )
    task_out = ComputationTaskOut.parse_obj(response.json())

    # check the contents is correctb
    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.NOT_STARTED,
        exp_pipeline_details=fake_workbench_computational_pipeline_details_not_started,
    )

    # start it now
    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=True,
        expected_response_status_code=status.HTTP_201_CREATED,
    )
    task_out = ComputationTaskOut.parse_obj(response.json())
    # check the contents is correctb
    _assert_computation_task_out_obj(
        client,
        task_out,
        project=sleepers_project,
        exp_task_state=RunningState.PUBLISHED,
        exp_pipeline_details=fake_workbench_computational_pipeline_details,
    )

    # wait until the pipeline is started
    task_out = _assert_pipeline_status(
        client,
        task_out.url,
        user_id,
        sleepers_project.uuid,
        wait_for_states=[RunningState.STARTED],
    )
    assert (
        task_out.state == RunningState.STARTED
    ), f"pipeline is not in the expected starting state but in {task_out.state}"

    # now try to update the pipeline, is expected to be forbidden
    response = _create_pipeline(
        client,
        project=sleepers_project,
        user_id=user_id,
        start_pipeline=False,
        expected_response_status_code=status.HTTP_403_FORBIDDEN,
    )

    # try to delete the pipeline, is expected to be forbidden if force parameter is false (default)
    response = client.delete(task_out.url, json={"user_id": user_id})
    assert (
        response.status_code == status.HTTP_403_FORBIDDEN
    ), f"response code is {response.status_code}, error: {response.text}"

    # try again with force=True this should abort and delete the pipeline
    response = client.delete(task_out.url, json={"user_id": user_id, "force": True})
    assert (
        response.status_code == status.HTTP_204_NO_CONTENT
    ), f"response code is {response.status_code}, error: {response.text}"


def test_pipeline_with_no_comp_services_still_create_correct_comp_tasks(
    client: TestClient,
    user_id: PositiveInt,
    project: Callable,
    jupyter_service: Dict[str, str],
):
    # create a workbench with just a dynamic service
    project_with_dynamic_node = project(
        workbench={
            "39e92f80-9286-5612-85d1-639fa47ec57d": {
                "key": jupyter_service["image"]["name"],
                "version": jupyter_service["image"]["tag"],
                "label": "my sole dynamic service",
            }
        }
    )

    # this pipeline is not runnable as there are no computational services
    response = _create_pipeline(
        client,
        project=project_with_dynamic_node,
        user_id=user_id,
        start_pipeline=True,
        expected_response_status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )

    # still this pipeline shall be createable if we do not want to start it
    response = _create_pipeline(
        client,
        project=project_with_dynamic_node,
        user_id=user_id,
        start_pipeline=False,
        expected_response_status_code=status.HTTP_201_CREATED,
    )
    assert (
        response.status_code == status.HTTP_201_CREATED
    ), f"response code is {response.status_code}, error: {response.text}"


def test_pipeline_with_control_pipeline_made_of_dynamic_services_are_allowed(
    client: TestClient,
    user_id: PositiveInt,
    project: Callable,
    jupyter_service: Dict[str, str],
):
    # create a workbench with just 2 dynamic service in a cycle
    project_with_dynamic_node = project(
        workbench={
            "39e92f80-9286-5612-85d1-639fa47ec57d": {
                "key": jupyter_service["image"]["name"],
                "version": jupyter_service["image"]["tag"],
                "label": "the controller",
                "inputs": {
                    "input_1": {
                        "nodeUuid": "09b92a4b-8bf4-49ad-82d3-1855c5a4957a",
                        "output": "output_1",
                    }
                },
                "inputNodes": ["09b92a4b-8bf4-49ad-82d3-1855c5a4957a"],
            },
            "09b92a4b-8bf4-49ad-82d3-1855c5a4957a": {
                "key": jupyter_service["image"]["name"],
                "version": jupyter_service["image"]["tag"],
                "label": "the model",
                "inputs": {
                    "input_1": {
                        "nodeUuid": "39e92f80-9286-5612-85d1-639fa47ec57d",
                        "output": "output_1",
                    }
                },
                "inputNodes": ["39e92f80-9286-5612-85d1-639fa47ec57d"],
            },
        }
    )

    # this pipeline is not runnable as there are no computational services
    response = client.post(
        COMPUTATION_URL,
        json={
            "user_id": user_id,
            "project_id": str(project_with_dynamic_node.uuid),
            "start_pipeline": True,
        },
    )
    assert (
        response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    ), f"response code is {response.status_code}, error: {response.text}"

    # still this pipeline shall be createable if we do not want to start it
    response = client.post(
        COMPUTATION_URL,
        json={
            "user_id": user_id,
            "project_id": str(project_with_dynamic_node.uuid),
            "start_pipeline": False,
        },
    )
    assert (
        response.status_code == status.HTTP_201_CREATED
    ), f"response code is {response.status_code}, error: {response.text}"


def test_pipeline_with_cycle_containing_a_computational_service_is_forbidden(
    client: TestClient,
    user_id: PositiveInt,
    project: Callable,
    sleeper_service: Dict[str, str],
    jupyter_service: Dict[str, str],
):
    # create a workbench with just 2 dynamic service in a cycle
    project_with_cycly_and_comp_service = project(
        workbench={
            "39e92f80-9286-5612-85d1-639fa47ec57d": {
                "key": jupyter_service["image"]["name"],
                "version": jupyter_service["image"]["tag"],
                "label": "the controller",
                "inputs": {
                    "input_1": {
                        "nodeUuid": "09b92a4b-8bf4-49ad-82d3-1855c5a4957c",
                        "output": "output_1",
                    }
                },
                "inputNodes": ["09b92a4b-8bf4-49ad-82d3-1855c5a4957c"],
            },
            "09b92a4b-8bf4-49ad-82d3-1855c5a4957a": {
                "key": jupyter_service["image"]["name"],
                "version": jupyter_service["image"]["tag"],
                "label": "the model",
                "inputs": {
                    "input_1": {
                        "nodeUuid": "39e92f80-9286-5612-85d1-639fa47ec57d",
                        "output": "output_1",
                    }
                },
                "inputNodes": ["39e92f80-9286-5612-85d1-639fa47ec57d"],
            },
            "09b92a4b-8bf4-49ad-82d3-1855c5a4957c": {
                "key": sleeper_service["image"]["name"],
                "version": sleeper_service["image"]["tag"],
                "label": "the computational service",
                "inputs": {
                    "input_1": {
                        "nodeUuid": "09b92a4b-8bf4-49ad-82d3-1855c5a4957a",
                        "output": "output_1",
                    }
                },
                "inputNodes": ["09b92a4b-8bf4-49ad-82d3-1855c5a4957a"],
            },
        }
    )

    # this pipeline is not runnable as there are no computational services and it contains a cycle
    response = client.post(
        COMPUTATION_URL,
        json={
            "user_id": user_id,
            "project_id": str(project_with_cycly_and_comp_service.uuid),
            "start_pipeline": True,
        },
    )
    assert (
        response.status_code == status.HTTP_403_FORBIDDEN
    ), f"response code is {response.status_code}, error: {response.text}"

    # still this pipeline shall be createable if we do not want to start it
    response = client.post(
        COMPUTATION_URL,
        json={
            "user_id": user_id,
            "project_id": str(project_with_cycly_and_comp_service.uuid),
            "start_pipeline": False,
        },
    )
    assert (
        response.status_code == status.HTTP_201_CREATED
    ), f"response code is {response.status_code}, error: {response.text}"


async def test_burst_create_computations(
    async_client: AsyncClient,
    user_id: PositiveInt,
    project: Callable,
    fake_workbench_without_outputs: Dict[str, Any],
    update_project_workbench_with_comp_tasks: Callable,
    fake_workbench_computational_pipeline_details: PipelineDetails,
    fake_workbench_computational_pipeline_details_completed: PipelineDetails,
):
    sleepers_project = project(workbench=fake_workbench_without_outputs)
    sleepers_project2 = project(workbench=fake_workbench_without_outputs)

    async def _create_pipeline(project: ProjectAtDB, start_pipeline: bool):
        return await async_client.post(
            COMPUTATION_URL,
            json={
                "user_id": user_id,
                "project_id": str(project.uuid),
                "start_pipeline": start_pipeline,
            },
            timeout=60,
        )

    NUMBER_OF_CALLS = 4

    # creating 4 pipelines without starting should return 4 times 201
    # the second pipeline should also return a 201
    responses = await asyncio.gather(
        *(
            [
                _create_pipeline(sleepers_project, start_pipeline=False)
                for _ in range(NUMBER_OF_CALLS)
            ]
            + [_create_pipeline(sleepers_project2, start_pipeline=False)]
        )
    )
    received_status_codes = [r.status_code for r in responses]
    assert status.HTTP_201_CREATED in received_status_codes
    assert received_status_codes.count(status.HTTP_201_CREATED) == NUMBER_OF_CALLS + 1

    # starting 4 pipelines should return 1 time 201 and 3 times 403
    # the second pipeline should return a 201
    responses = await asyncio.gather(
        *(
            [
                _create_pipeline(sleepers_project, start_pipeline=True)
                for _ in range(NUMBER_OF_CALLS)
            ]
            + [_create_pipeline(sleepers_project2, start_pipeline=False)]
        )
    )
    received_status_codes = [r.status_code for r in responses]
    assert received_status_codes.count(status.HTTP_201_CREATED) == 2
    assert received_status_codes.count(status.HTTP_403_FORBIDDEN) == (
        NUMBER_OF_CALLS - 1
    )