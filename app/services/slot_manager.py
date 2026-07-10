# app/services/slot_manager.py
import structlog
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import VPNService
from app.config import get_settings

logger = structlog.get_logger(__name__)

async def get_least_populated_personal_slot(session: AsyncSession) -> str:
    """
    Queries the database and dynamically load-balances active subscriptions
    across your 5 pre-configured personal Control D endpoints [cite: 1].
    """
    settings = get_settings()
    
    # Collate configured slot IDs
    slots_pool = [
        settings.controld_device_1,
        settings.controld_device_2,
        settings.controld_device_3,
        settings.controld_device_4,
        settings.controld_device_5,
    ]
    
    # Filter empty settings
    slots = [s.strip() for s in slots_pool if s and s.strip()]
    if not slots:
        raise ValueError("No personal CONTROLD_DEVICE_X slots are configured in Settings/env")

    # Map initial counts to 0 for all slots
    slot_counts = {slot: 0 for slot in slots}

    # Query active subscriptions grouped by device slot
    stmt = (
        select(VPNService.controld_device_id, func.count(VPNService.id))
        .where(
            VPNService.status == "active",
            VPNService.controld_device_id.in_(slots)
        )
        .group_by(VPNService.controld_device_id)
    )
    result = await session.execute(stmt)
    
    for row in result.all():
        device_id, count = row
        if device_id in slot_counts:
            slot_counts[device_id] = count

    # Determine slot with the fewest active users
    assigned_slot = min(slot_counts, key=slot_counts.get)
    logger.info("slot_successfully_allocated", slot=assigned_slot, active_subscriptions=slot_counts[assigned_slot])
    return assigned_slot