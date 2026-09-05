# app/services/ip_manager.py
from datetime import datetime, timezone
from sqlalchemy import select  # <-- FIXED
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import VPNService
from app.services.adguard import AdGuardHomeService
from app.services.controld import ControlDService

logger = structlog.get_logger(__name__)

async def update_device_ip_safe(session: AsyncSession, service: VPNService, new_ip: str) -> bool:
    if not service:
        return False

    # 1. Validation & Safety Checks
    now = datetime.now(timezone.utc)
    expire_at = service.expire_at
    if expire_at:
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        if expire_at <= now or service.status == "disabled":
            logger.warning("refusing_ip_update_for_expired_or_disabled_service", service_id=service.id)
            return False

    device_id = service.controld_device_id
    if not device_id:
        logger.error("ip_swap_failed_missing_device_id", service_id=service.id)
        return False

    old_ip = service.authorized_ip
    clean_new_ip = new_ip.strip()

    settings = get_settings()
    controld = ControlDService(settings)
    adguard = AdGuardHomeService(settings)

    # 2. Deauthorize Old IP if it exists and has changed
    if old_ip and old_ip != clean_new_ip:
        # NEW CHECK: Are any other active users sharing the old IP?
        active_shared_ip_stmt = select(VPNService).where(
            VPNService.authorized_ip == old_ip,
            VPNService.status == "active",
            VPNService.id != service.id
        ).limit(1)
        has_active_sharers = (await session.execute(active_shared_ip_stmt)).scalars().first()
        
        if not has_active_sharers:
            # Deauthorize from Control D
            try:
                logger.info("deauthorizing_old_ip_controld", service_id=service.id, device_id=device_id, old_ip=old_ip)
                await controld.deauthorize_ip(device_id, old_ip)
            except Exception as exc:
                logger.warning("controld_old_ip_deauth_failed_proceeding", service_id=service.id, error=str(exc))

            # Deauthorize from AdGuard Home
            if adguard.is_configured():
                try:
                    logger.info("deauthorizing_old_ip_adguard", service_id=service.id, old_ip=old_ip)
                    await adguard.deauthorize_client_ip(old_ip)
                except Exception as exc:
                    logger.warning("adguard_old_ip_deauth_failed_proceeding", service_id=service.id, error=str(exc))
        else:
            logger.info(
                "skipping_old_ip_deauthorization_shared_by_active_service",
                service_id=service.id,
                old_ip=old_ip,
            )

    # 3. Authorize New IP on Control D
    controld_success = False
    try:
        logger.info("authorizing_new_ip_controld", service_id=service.id, device_id=device_id, new_ip=clean_new_ip)
        controld_success = await controld.authorize_ip(device_id, clean_new_ip)
    except Exception as exc:
        logger.error("controld_new_ip_auth_failed", service_id=service.id, error=str(exc))

    if not controld_success:
        logger.error("controld_auth_unsuccessful_aborting_db_sync", service_id=service.id, new_ip=clean_new_ip)
        return False

    # 4. Authorize New IP on AdGuard Home (inside ip_manager.py)
    if adguard.is_configured():
        try:
            logger.info("authorizing_new_ip_adguard", service_id=service.id, new_ip=clean_new_ip)
            
            # Step A: Allow the IP through the global firewall
            await adguard.allow_client_ip(clean_new_ip)
            
            # Step B: Tie this single IP strictly to their AdGuard Client profile
            username = service.user.username if service.user and service.user.username else f"u{service.user_id}"
            await adguard.sync_user_client(service.id, username, clean_new_ip)
            
        except Exception as exc:
            logger.warning("adguard_new_ip_auth_failed_non_fatal", service_id=service.id, error=str(exc))
            
    # 5. Commit updated IP to database
    try:
        service.authorized_ip = clean_new_ip
        await session.commit()
        logger.info("dual_ip_sync_successful", service_id=service.id, old_ip=old_ip, new_ip=clean_new_ip)
        return True
    except Exception as exc:
        await session.rollback()
        logger.error("failed_to_commit_ip_swap_transaction", service_id=service.id, error=str(exc))
        return False
