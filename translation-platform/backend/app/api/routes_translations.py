import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.repositories.audit_repo import AuditRepository

logger = logging.getLogger(__name__)
router = APIRouter(tags=["audit"])


@router.get("/audit-logs")
async def list_audit_logs(
    limit: int = Query(100, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List audit log entries (most recent first)."""
    repo = AuditRepository(db)
    logs = await repo.list_all(limit=limit)

    return [
        {
            "id": str(log.id),
            "event_type": log.event_type,
            "metadata": log.metadata_,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
