from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from src.models import HealthCheck
from src.modules.api.base import ErrorHandlingBaseRoute
from src.modules.auth.dependencies import APIAccess
from src.modules.auth.permissions import Permission

__all__ = ("router",)


router = APIRouter(
    dependencies=[Depends(APIAccess(Permission.SYSTEM_API))],
    prefix="/system",
    tags=["system"],
    responses={404: {"description": "Not found"}},
    route_class=ErrorHandlingBaseRoute,
)


@router.get("/health/", response_model=HealthCheck)
async def health_check() -> HealthCheck:
    """Health check endpoint."""
    return HealthCheck(
        status="healthy",
        timestamp=datetime.now(UTC),
    )
