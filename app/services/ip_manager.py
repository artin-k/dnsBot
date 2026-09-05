# app/services/ip_manager.py
from datetime import datetime, timezone
import structlog
from sqlalchemy import select
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User, VPNService, VPNServiceStatus
from app.services.adguard import AdGuardHomeService
from app.services.controld import ControlDService

logger = structlog.get_logger(__name__)


def _clean_adguard_username(username: str | None, fallback: str) -> str:
    raw = (username or fallback).strip()
    cleaned = "".join(c for c in raw if c.isalnum() or c in ("_", "-"))
    return cleaned or fallback


async def _resolve_adguard_username(session: AsyncSession, service: VPNService) -> str:
    fallback = f"u{service.user_id}"
    username: str | None = None

    try:
        username = service.username
    except MissingGreenlet as exc:
        logger.warning(
            "adguard_service_username_missing_greenlet",
            service_id=service.id,
            error=str(exc),
        )

    if not username:
        try:
            user_obj = service.__dict__.get("user")
            if user_obj is None:
                user_obj = service.user

            if user_obj is not None:
                username = (
                    getattr(user_obj, "username", None)
                    or getattr(user_obj, "telegram_username", None)
                    or getattr(user_obj, "first_name", None)
                )
        except MissingGreenlet as exc:
            logger.warning(
                "adguard_username_lazy_load_missing_greenlet",
                service_id=service.id,
                error=str(exc),
            )
        except Exception as exc:
            logger.warning("adguard_username_relationship_read_failed", service_id=service.id, error=str(exc))

    if not username:
        try:
            username = await session.scalar(select(User.telegram_username).where(User.id == service.user_id))
        except Exception as exc:
            logger.warning("adguard_username_db_lookup_failed", service_id=service.id, error=str(exc))

    return _clean_adguard_username(username, fallback)


async def _has_active_ip_sharer(session: AsyncSession, ip_address: str, service_id: int) -> bool:
    active_shared_ip_stmt = (
        select(VPNService.id)
        .where(
            VPNService.authorized_ip == ip_address,
            VPNService.status == VPNServiceStatus.ACTIVE.value,
            VPNService.id != service_id,
        )
        .limit(1)
    )
    return (await session.scalar(active_shared_ip_stmt)) is not None


async def update_device_ip_safe(session: AsyncSession, service: VPNService, new_ip: str) -> bool:
    if not service:
        return False

    # 1. Validation & Safety Checks
    now = datetime.now(timezone.utc)
    expire_at = service.expire_at
    if expire_at:
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        if expire_at <= now or service.status == VPNServiceStatus.DISABLED.value:
            logger.warning("refusing_ip_update_for_expired_or_disabled_service", service_id=service.id)
            return False

    device_id = service.controld_device_id
    if not device_id:
        logger.error("ip_swap_failed_missing_device_id", service_id=service.id)
        return False

    old_ip = service.authorized_ip
    clean_new_ip = new_ip.strip()
    if not clean_new_ip:
        logger.warning("ip_swap_failed_empty_new_ip", service_id=service.id)
        return False

    settings = get_settings()
    controld = ControlDService(settings)
    adguard = AdGuardHomeService(settings)

    # -------------------------------------------------------------------------
    # 2. PRIMARY: Authorize New IP on Control D
    # -------------------------------------------------------------------------
    controld_success = False
    try:
        logger.info("authorizing_new_ip_controld", service_id=service.id, device_id=device_id, new_ip=clean_new_ip)
        controld_success = await controld.authorize_ip(device_id, clean_new_ip)
    except Exception as exc:
        logger.error("controld_new_ip_auth_failed", service_id=service.id, error=str(exc))

    if not controld_success:
        logger.error("controld_auth_unsuccessful_aborting_sync", service_id=service.id, new_ip=clean_new_ip)
        return False

    # -------------------------------------------------------------------------
    # 3. SECONDARY: Sync New IP & Client Object on AdGuard Home
    # -------------------------------------------------------------------------
    if adguard.is_configured():
        try:
            username = await _resolve_adguard_username(session, service)
            logger.info("authorizing_new_ip_adguard", service_id=service.id, new_ip=clean_new_ip)

            adguard_allowed = await adguard.allow_client_ip(clean_new_ip)
            if not adguard_allowed:
                logger.error("adguard_new_ip_allow_failed_aborting_sync", service_id=service.id, new_ip=clean_new_ip)
                if old_ip != clean_new_ip and not await _has_active_ip_sharer(session, clean_new_ip, service.id):
                    await controld.deauthorize_ip(device_id, clean_new_ip)
                return False

            adguard_client_synced = await adguard.sync_user_client(service.id, username, clean_new_ip)
            if not adguard_client_synced:
                logger.error("adguard_persistent_client_sync_failed_aborting", service_id=service.id, new_ip=clean_new_ip)
                if old_ip != clean_new_ip and not await _has_active_ip_sharer(session, clean_new_ip, service.id):
                    try:
                        await controld.deauthorize_ip(device_id, clean_new_ip)
                    except Exception as exc:
                        logger.warning("controld_new_ip_cleanup_failed", service_id=service.id, error=str(exc))
                    try:
                        await adguard.deauthorize_client_ip(clean_new_ip)
                    except Exception as exc:
                        logger.warning("adguard_new_ip_cleanup_failed", service_id=service.id, error=str(exc))
                return False

        except Exception as exc:
            logger.error("adguard_sync_failed_aborting", service_id=service.id, error=str(exc))
            if old_ip != clean_new_ip and not await _has_active_ip_sharer(session, clean_new_ip, service.id):
                try:
                    await controld.deauthorize_ip(device_id, clean_new_ip)
                except Exception as cleanup_exc:
                    logger.warning("controld_new_ip_cleanup_failed", service_id=service.id, error=str(cleanup_exc))
            return False

    # -------------------------------------------------------------------------
    # 4. CLEANUP: Deauthorize Old IP if Changed (Guarded by Shared-Slot Check)
    # -------------------------------------------------------------------------
    if old_ip and old_ip != clean_new_ip:
        if not await _has_active_ip_sharer(session, old_ip, service.id):
            # Drop from Control D
            try:
                logger.info("deauthorizing_old_ip_controld", service_id=service.id, device_id=device_id, old_ip=old_ip)
                await controld.deauthorize_ip(device_id, old_ip)
            except Exception as exc:
                logger.warning("controld_old_ip_deauth_failed_proceeding", service_id=service.id, error=str(exc))

            # Drop from AdGuard global whitelist
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

    # -------------------------------------------------------------------------
    # 5. Database Commit
    # -------------------------------------------------------------------------
    try:
        service.authorized_ip = clean_new_ip
        await session.commit()
        logger.info("dual_ip_sync_successful", service_id=service.id, old_ip=old_ip, new_ip=clean_new_ip)
        return True
    except Exception as exc:
        await session.rollback()
        logger.error("failed_to_commit_ip_swap_transaction", service_id=service.id, error=str(exc))
        return False
