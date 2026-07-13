# app/services/ip_manager.py
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import VPNService
from app.services.controld import ControlDService
from app.config import get_settings

logger = structlog.get_logger(__name__)

async def update_device_ip_safe(session: AsyncSession, service: VPNService, new_ip: str) -> bool:
    """
    Surgically swaps a user's authorized IP on their ControlD endpoint [cite: 1].
    1. Deauthorizes the OLD IP from the OLD ControlD device slot (DELETE /access) [cite: 1].
    2. Authorizes the NEW IP on the ControlD device slot (POST /access) [cite: 1].
    3. Saves changes to the database and commits atomically [cite: 1].
    """
    settings = get_settings()
    controld = ControlDService(settings)
    device_id = service.controld_device_id
    old_ip = service.authorized_ip

    if not device_id:
        logger.error("ip_swap_failed_missing_device_id", service_id=service.id)
        return False

    # Avoid redundant updates if the IP has not changed
    if old_ip == new_ip:
        logger.info("ip_already_authorized_on_endpoint_bypassing", service_id=service.id, ip=new_ip)
        return True

    # Step 1: Deauthorize the old IP from the endpoint (DELETE) [cite: 1]
    if old_ip:
        try:
            logger.info("surgically_deauthorizing_old_ip", service_id=service.id, device_id=device_id, old_ip=old_ip)
            await controld.deauthorize_ip(device_id, old_ip)
        except Exception as exc:
            # Log warning but do NOT block authorization of the new IP
            logger.warning("old_ip_deauthorization_failed_proceeding", service_id=service.id, error=str(exc))

    # Step 2: Authorize the new IP on the endpoint (POST) [cite: 1]
    auth_success = False
    try:
        logger.info("authorizing_new_ip", service_id=service.id, device_id=device_id, new_ip=new_ip)
        auth_success = await controld.authorize_ip(device_id, new_ip)
    except Exception as exc:
        logger.error("new_ip_authorization_failed", service_id=service.id, error=str(exc))

    if not auth_success:
        logger.error("api_authorization_failed_aborting_db_sync", service_id=service.id, new_ip=new_ip)
        return False

    # Step 3: Update database and commit atomically [cite: 1]
    try:
        service.authorized_ip = new_ip
        await session.commit()
        logger.info("ip_swap_completed_successfully", service_id=service.id, old_ip=old_ip, new_ip=new_ip)
        return True
    except Exception as exc:
        await session.rollback()
        logger.error("failed_to_commit_ip_swap_db_transaction", service_id=service.id, error=str(exc))
        return False