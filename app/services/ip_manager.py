# app/services/ip_manager.py
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import VPNService
from app.services.controld import ControlDService
from app.config import get_settings, SLOT_CONFIGS

logger = structlog.get_logger(__name__)

async def update_device_ip_safe(session: AsyncSession, service: VPNService, new_ip: str) -> bool:
    """
    Enforces a strict 1-IP limit per subscription across your entire account [cite: 1].
    1. Deauthorizes their OLD registered IP from the current slot first [cite: 1].
    2. Forcefully deauthorizes their NEW IP from the other 4 slots to prevent duplicates [cite: 1].
    3. Authorizes their NEW IP on the current slot [cite: 1].
    4. Updates the local DB and commits changes [cite: 1].
    """
    settings = get_settings()
    controld = ControlDService(settings)
    device_id = service.controld_device_id
    
    if not device_id:
        logger.error("ip_update_failed_missing_device_id", service_id=service.id)
        return False

    old_ip = service.authorized_ip

    # If the user's connection hasn't changed, bypass unneeded API calls
    if old_ip == new_ip:
        logger.info("ip_already_authorized_bypassing", service_id=service.id, ip=new_ip)
        return True

    # Step 1: Delete their OLD registered IP from the current device slot [cite: 1]
    if old_ip:
        logger.info("deauthorizing_old_ip_from_current_slot", device_id=device_id, old_ip=old_ip)
        # Cleanly deletes the old IP from Control D [cite: 1]
        await controld.deauthorize_ip(device_id, old_ip)

    # Step 2: Forcefully delete their NEW IP from the other 4 slots for account-wide safety [cite: 1]
    for slot_num, config in SLOT_CONFIGS.items():
        other_device_id = config["device_id"]
        if other_device_id and other_device_id != device_id:
            logger.info("force_cleaning_new_ip_from_other_slot", other_device_id=other_device_id, ip=new_ip)
            await controld.deauthorize_ip(other_device_id, new_ip)

    # Step 3: Authorize their NEW IP on the current slot [cite: 1]
    logger.info("authorizing_new_ip_on_current_slot", device_id=device_id, ip=new_ip)
    auth_success = await controld.authorize_ip(device_id, new_ip)
    
    if not auth_success:
        logger.error("api_authorization_failed", service_id=service.id, new_ip=new_ip)
        return False

    # Step 4: Update the database and commit [cite: 1]
    service.authorized_ip = new_ip
    await session.commit()
    logger.info("ip_successfully_replaced_remotely_and_locally", service_id=service.id, new_ip=new_ip)
    return True