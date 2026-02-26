#  Orchestration Engine - Service Health Routes
#
#  Resource health check endpoints.
#
#  Depends on: container.py, models/schemas.py
#  Used by:    app.py

from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, HTTPException

from backend.container import Container
from backend.models.schemas import ResourceOut
from backend.services.resource_monitor import ResourceMonitor

router = APIRouter(prefix="/services", tags=["services"])


@router.get("")
@inject
async def list_services(
    resource_monitor: ResourceMonitor = Depends(Provide[Container.resource_monitor]),
) -> list[ResourceOut]:
    """Get health status of all resources (Ollama, ComfyUI, Claude API)."""
    # Force a fresh check
    states = await resource_monitor.check_all()
    return [
        ResourceOut(
            id=s.id,
            name=s.name,
            status=s.status,
            method=s.method,
            details=s.details,
            category=s.category,
        )
        for s in states
    ]


@router.get("/{resource_id}")
@inject
async def get_service(
    resource_id: str,
    resource_monitor: ResourceMonitor = Depends(Provide[Container.resource_monitor]),
) -> ResourceOut:
    """Get health status of a single resource."""
    state = resource_monitor.get(resource_id)
    if not state:
        raise HTTPException(404, f"Resource {resource_id} not found")
    return ResourceOut(
        id=state.id,
        name=state.name,
        status=state.status,
        method=state.method,
        details=state.details,
        category=state.category,
    )
