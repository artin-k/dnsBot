# app/services/scheduler.py
import asyncio
from datetime import datetime, timezone
import structlog
from aiogram import Bot
from sqlalchemy import select, delete
from sqlalchemy.orm import joinedload

from app.database import async_session_maker
from app.config import get_settings
from app.services.controld import ControlDService
from app.models import Plan, VPNService, VPNServiceStatus, IPAuthToken
from app.utils.formatting import format_datetime

logger = structlog.get_logger(__name__)

async def cleanup_expired_dns_services(bot: Bot | None = None) -> int:
    """
    Finds expired active DNS services, calls Control D to deauthorize their mapped 
    IP address to cut off access immediately, and transitions their DB status to 'expired' [cite: 1].
    """
    now = datetime.now(timezone.utc)
    settings = get_settings()
    cd_service = ControlDService(settings)
    processed = 0

    async with async_session_maker() as session:
        try:
            stmt = (
                select(VPNService)
                .options(joinedload(VPNService.user))
                .where(
                    VPNService.status == VPNServiceStatus.ACTIVE.value,
                    VPNService.expire_at <= now,
                )
                .order_by(VPNService.expire_at.asc(), VPNService.id.asc())
            )
            result = await session.execute(stmt)
            expired_services = list(result.scalars().unique().all())
        except Exception as e:
            logger.error("failed_to_query_expired_services", error=str(e))
            return 0

        if not expired_services:
            return 0

        logger.info("checking_expired_dns_services", count=len(expired_services))

        for service in expired_services:
            logger.info("processing_personal_expiration", service_id=service.id, username=service.username)
            
            try:
                # Isolate operations using a nested transaction
                async with session.begin_nested():
                    service_expire = service.expire_at
                    if service_expire.tzinfo is None:
                        service_expire = service_expire.replace(tzinfo=timezone.utc)

                    if service_expire > now:
                        logger.warning("skipping_non_expired_service_safety_check", service_id=service.id)
                        continue

                    # If an authorized IP is registered, deauthorize it immediately on Control D [cite: 1]
                    if service.controld_device_id and service.authorized_ip:
                        logger.info(
                            "deauthorizing_expired_ip_on_controld",
                            service_id=service.id,
                            device_id=service.controld_device_id,
                            ip=service.authorized_ip,
                        )
                        try:
                            # Performs DELETE request to deauthorize IP [cite: 1]
                            success = await cd_service.deauthorize_ip(
                                device_id=service.controld_device_id, 
                                ip=service.authorized_ip
                            )
                        except Exception as exc:
                            logger.error(
                                "controld_ip_deauthorization_raised_exception",
                                service_id=service.id,
                                error=str(exc),
                            )
                            success = False

                        if not success:
                            logger.warning(
                                "failed_to_deauthorize_expired_ip_remotely_proceeding_locally", 
                                service_id=service.id
                            )

                    # Update local database status
                    service.status = VPNServiceStatus.EXPIRED.value
                    
                    # Cascade delete any associated IP auth tokens so they can't be used after expiration
                    await session.execute(
                        delete(IPAuthToken).where(IPAuthToken.service_id == service.id)
                    )

                # Commit changes for this individual subscription
                await session.commit()
                processed += 1

                # Send Telegram notification to the user
                if bot is not None and service.user is not None:
                    try:
                        title_label = "اکانت تست" if service.is_test_account else "اشتراک"
                        await bot.send_message(
                            chat_id=service.user.telegram_id,
                            text=(
                                f"⏳ <b>{title_label} DNS شما به پایان رسید.</b>\n\n"
                                f"🗓 <b>تاریخ انقضاء:</b> {format_datetime(service.expire_at)}\n"
                                "دسترسی شما غیرفعال و آی‌پی شما از پنل حذف شد. "
                                "برای خرید مجدد یا تمدید می‌توانید از منوی اصلی اقدام کنید."
                            ),
                            parse_mode="HTML"
                        )
                    except Exception as exc:
                        logger.warning("failed_to_notify_expired_service_owner", service_id=service.id, error=str(exc))

            except Exception as e:
                await session.rollback()
                logger.error("failed_to_expire_personal_service_individually", service_id=service.id, error=str(e))
                continue

        return processed

# app/services/scheduler.py (Add this at the bottom)

async def restrict_all_shared_slots() -> None:
    """
    Finds your 5 permanent shared device slots and enforces restricted whitelisting
    mode on each of them during bot startup [cite: 1].
    """
    settings = get_settings()
    controld = ControlDService(settings)
    
    from app.config import SLOT_CONFIGS
    logger.info("initiating_automated_endpoint_restriction_check")
    
    for slot_num, config in SLOT_CONFIGS.items():
        device_id = config["device_id"]
        if device_id and len(device_id) > 3:
            logger.info("enforcing_restricted_whitelisting_on_slot", slot=slot_num, device_id=device_id)
            await controld.restrict_device(device_id)

async def sync_plans_with_controld(session) -> None:
    """
    Synchronizes your Control D dashboard Profiles with local Plans in PostgreSQL.
    """
    settings = get_settings()
    cd_service = ControlDService(settings)
    
    profiles = await cd_service.fetch_controld_profiles()
    if not profiles:
        logger.warning("no_controld_profiles_found_or_sync_failed")
        return

    logger.info("syncing_controld_profiles_to_database", count=len(profiles))

    for profile in profiles:
        profile_id = profile["id"]
        profile_name = profile["name"]
        profile_desc = profile["description"] or "سرویس دی‌ان‌اس اختصاصی"

        stmt = select(Plan).where(Plan.controld_profile_id == profile_id)
        result = await session.execute(stmt)
        existing_plan = result.scalars().first()

        if existing_plan is None:
            new_plan = Plan(
                title=profile_name,
                description=profile_desc,
                duration_hours=720,         # Default: 30 days = 720 hours
                volume_gb=0,                # DNS has no volume limit
                price=50000,                # Default price (Toman)
                is_active=True,
                sort_order=0,
                controld_profile_id=profile_id
            )
            session.add(new_plan)
            logger.info("synced_new_dns_plan", title=profile_name, id=profile_id)
        else:
            existing_plan.title = profile_name
            if profile["description"]:
                existing_plan.description = profile_desc
                
    await session.commit()


async def expiration_scheduler_loop(bot: Bot | None = None, interval_seconds: int = 3600) -> None:
    """
    Background automation worker that runs continuously [cite: 1].
    Queries the database on a set interval (default: every hour / 3600 seconds) [cite: 1].
    """
    logger.info("starting_expiration_scheduler_loop", interval_seconds=interval_seconds)
    while True:
        try:
            processed = await cleanup_expired_dns_services(bot=bot)
            if processed:
                logger.info("expired_personal_dns_services_processed", count=processed)
        except Exception as e:
            logger.error("expiration_scheduler_loop_error", error=str(e))
        
        await asyncio.sleep(interval_seconds)