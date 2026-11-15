from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from mortis.config.store import ConfigStore
from mortis.services import Services
from mortis.tasks.runner import TaskRunner


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_config_store(request: Request) -> ConfigStore:
    return request.app.state.config_store


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_runner(request: Request) -> TaskRunner:
    return request.app.state.task_runner


SessionDep = Annotated[Session, Depends(get_session)]
ConfigStoreDep = Annotated[ConfigStore, Depends(get_config_store)]
ServicesDep = Annotated[Services, Depends(get_services)]
RunnerDep = Annotated[TaskRunner, Depends(get_runner)]
