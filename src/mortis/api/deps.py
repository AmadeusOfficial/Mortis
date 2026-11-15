from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from mortis.config.store import ConfigStore


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_config_store(request: Request) -> ConfigStore:
    return request.app.state.config_store


SessionDep = Annotated[Session, Depends(get_session)]
ConfigStoreDep = Annotated[ConfigStore, Depends(get_config_store)]
