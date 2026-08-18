# app/services/ip_manager.py

import structlog
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import VPNService
from app.services.controld import ControlDService
from app.config import get_settings

logger = structlog.get_logger(__name__)

async def update_device_ip_safe(session: AsyncSession, service: VPNService, new_ip: str) -> bool:
    """
    Surgically swaps a user's authorized IP on their ControlD endpoint.
    1. Deauthorizes old_ip from current slot if old_ip != new_ip.
    2. Authorizes new_ip on current slot.
    3. Saves service.authorized_ip = new_ip in database.
    """
    if not service:
        return False

    now = datetime.now(timezone.utc)
    expire_at = service.expire_at
    if expire_at:
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        if expire_at <= now or service.status == "disabled":
            logger.warning("refusing_ip_update_for_expired_or_disabled_service", service_id=service.id)
            return False

    device_id = service.controld_device_id
    old_ip = service.authorized_ip
    clean_new_ip = new_ip.strip()

    if not device_id:
        logger.error("ip_swap_failed_missing_device_id", service_id=service.id)
        return False

    settings = get_settings()
    controld = ControlDService(settings)

    # 1. If old_ip is recorded and different from new_ip, deauthorize old_ip
    if old_ip and old_ip != clean_new_ip:
        try:
            logger.info("deauthorizing_old_ip_before_new_ip_swap", service_id=service.id, device_id=device_id, old_ip=old_ip)
            await controld.deauthorize_ip(device_id, old_ip)
        except Exception as exc:
            logger.warning("old_ip_deauthorization_failed_proceeding", service_id=service.id, error=str(exc))

    # 2. Authorize new_ip on current slot
    auth_success = False
    try:
        logger.info("authorizing_new_ip", service_id=service.id, device_id=device_id, new_ip=clean_new_ip)
        auth_success = await controld.authorize_ip(device_id, clean_new_ip)
    except Exception as exc:
        logger.error("new_ip_authorization_failed", service_id=service.id, error=str(exc))

    if not auth_success:
        logger.error("api_authorization_failed_aborting_db_sync", service_id=service.id, new_ip=clean_new_ip)
        return False

    # 3. Save new_ip to database
    try:
        service.authorized_ip = clean_new_ip
        await session.commit()
        logger.info("ip_swap_completed_successfully", service_id=service.id, old_ip=old_ip, new_ip=clean_new_ip)
        return True
    except Exception as exc:
        await session.rollback()
        logger.error("failed_to_commit_ip_swap_db_transaction", service_id=service.id, error=str(exc))
        return False