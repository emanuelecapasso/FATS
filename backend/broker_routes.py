"""REST API for broker configuration management."""

from datetime import date, time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend.dependencies import get_broker_repository
from data.broker_config import BrokerConfig
from database.broker_repository import BrokerRepository

router = APIRouter(
    prefix="/api/v1/brokers",
    tags=["brokers"],
)


class BrokerConfigFields(BaseModel):
    """Shared broker configuration fields."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    timezone: str = Field(default="America/New_York", min_length=1)
    sunday_open: time = time(17, 0)
    friday_close: time = time(17, 0)
    closed_dates: list[date] = Field(default_factory=list)


class BrokerCreateRequest(BrokerConfigFields):
    """Request body for registering a broker."""

    provider_id: str = Field(min_length=1)


class BrokerUpdateRequest(BrokerConfigFields):
    """Request body for updating a broker."""


class BrokerConfigResponse(BaseModel):
    """Public representation of a broker configuration."""

    provider_id: str
    name: str
    timezone: str
    sunday_open: time
    friday_close: time
    closed_dates: list[date]

    @classmethod
    def from_config(cls, config: BrokerConfig) -> "BrokerConfigResponse":
        return cls(
            provider_id=config.provider_id,
            name=config.name,
            timezone=config.timezone,
            sunday_open=config.sunday_open,
            friday_close=config.friday_close,
            closed_dates=sorted(config.closed_dates),
        )


def build_broker_config(
    provider_id: str,
    payload: BrokerConfigFields,
) -> BrokerConfig:
    """Convert and validate an API payload into a domain configuration."""
    try:
        return BrokerConfig(
            provider_id=provider_id,
            name=payload.name,
            timezone=payload.timezone,
            sunday_open=payload.sunday_open,
            friday_close=payload.friday_close,
            closed_dates=frozenset(payload.closed_dates),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.post(
    "",
    response_model=BrokerConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a broker",
)
def create_broker(
    payload: BrokerCreateRequest,
    repository: Annotated[
        BrokerRepository,
        Depends(get_broker_repository),
    ],
) -> BrokerConfigResponse:
    """Register a new broker configuration."""
    if repository.get(payload.provider_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Broker already exists: {payload.provider_id}",
        )

    config = build_broker_config(payload.provider_id, payload)
    repository.save(config)

    return BrokerConfigResponse.from_config(config)


@router.get(
    "",
    response_model=list[BrokerConfigResponse],
    summary="List configured brokers",
)
def list_brokers(
    repository: Annotated[
        BrokerRepository,
        Depends(get_broker_repository),
    ],
) -> list[BrokerConfigResponse]:
    """Return all registered broker configurations."""
    return [
        BrokerConfigResponse.from_config(config) for config in repository.list_all()
    ]


@router.get(
    "/{provider_id}",
    response_model=BrokerConfigResponse,
    summary="Retrieve a broker",
)
def get_broker(
    provider_id: str,
    repository: Annotated[
        BrokerRepository,
        Depends(get_broker_repository),
    ],
) -> BrokerConfigResponse:
    """Retrieve one broker configuration."""
    config = repository.get(provider_id)

    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Broker configuration not found: {provider_id}",
        )

    return BrokerConfigResponse.from_config(config)


@router.put(
    "/{provider_id}",
    response_model=BrokerConfigResponse,
    summary="Update a broker",
)
def update_broker(
    provider_id: str,
    payload: BrokerUpdateRequest,
    repository: Annotated[
        BrokerRepository,
        Depends(get_broker_repository),
    ],
) -> BrokerConfigResponse:
    """Replace the configuration of an existing broker."""
    if repository.get(provider_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Broker configuration not found: {provider_id}",
        )

    config = build_broker_config(provider_id, payload)
    repository.save(config)

    return BrokerConfigResponse.from_config(config)


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a broker configuration",
)
def delete_broker(
    provider_id: str,
    repository: Annotated[
        BrokerRepository,
        Depends(get_broker_repository),
    ],
) -> Response:
    """Delete a broker configuration without deleting market history."""
    deleted = repository.delete(provider_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Broker configuration not found: {provider_id}",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
