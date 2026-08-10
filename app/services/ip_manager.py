# app/services/ip_manager.py
from datetime import datetime, timezone
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import VPNService
from app.services.controld import ControlDService
from app.config import get_settings

logger = structlog.get_logger(__name__)

async def update_device_ip_safe(session: AsyncSession, service: VPNService, new_ip: str) -> bool:
    """
    Surgically swaps a user's authorized IP on their ControlD endpoint.
    Strictly checks subscription expiration before proceeding.
    """
    if not service:
        return False

    # Expiration and status verification
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

    settings = get_settings()
    controld = ControlDService(settings)

    if not device_id:
        logger.error("ip_swap_failed_missing_device_id", service_id=service.id)
        return False

    if old_ip == new_ip:
        logger.info("ip_already_authorized_on_endpoint_bypassing", service_id=service.id, ip=new_ip)
        return True

    if old_ip:
        try:
            logger.info("surgically_deauthorizing_old_ip", service_id=service.id, device_id=device_id, old_ip=old_ip)
            await controld.deauthorize_ip(device_id, old_ip)
        except Exception as exc:
            logger.warning("old_ip_deauthorization_failed_proceeding", service_id=service.id, error=str(exc))

    auth_success = False
    try:
        logger.info("authorizing_new_ip", service_id=service.id, device_id=device_id, new_ip=new_ip)
        auth_success = await controld.authorize_ip(device_id, new_ip)
    except Exception as exc:
        logger.error("new_ip_authorization_failed", service_id=service.id, error=str(exc))

    if not auth_success:
        logger.error("api_authorization_failed_aborting_db_sync", service_id=service.id, new_ip=new_ip)
        return False

    try:
        service.authorized_ip = new_ip
        await session.commit()
        logger.info("ip_swap_completed_successfully", service_id=service.id, old_ip=old_ip, new_ip=new_ip)
        return True
    except Exception as exc:
        await session.rollback()
        logger.error("failed_to_commit_ip_swap_db_transaction", service_id=service.id, error=str(exc))
        return False