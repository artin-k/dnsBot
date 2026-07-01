# app/services/scheduler.py
import asyncio
from datetime import datetime, timezone

import structlog
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.database import async_session_maker
from app.config import get_settings
from app.services.controld import ControlDService
from app.models import Plan, VPNService, VPNServiceStatus
from app.utils.formatting import format_datetime

logger = structlog.get_logger(__name__)


async def cleanup_expired_dns_services(bot: Bot | None = None) -> int:
    """
    Marks expired DNS services as expired in the database and best-effort deletes
    their Control D devices.

    Control D already enforces the hard stop through `disable_ttl`, so this loop
    acts as a durable reconciliation worker: it survives restarts, catches missed
    expirations, and keeps the database in sync.
    """
    now = datetime.now(timezone.utc)
    settings = get_settings()
    cd_service = ControlDService(settings)

    processed = 0
    async with async_session_maker() as session:
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

        if not expired_services:
            return 0

        logger.info("checking_expired_dns_services", count=len(expired_services))

        for service in expired_services:
            if service.controld_device_id:
                logger.info(
                    "deleting_controld_device",
                    service_id=service.id,
                    device_id=service.controld_device_id,
                )
                try:
                    success = await cd_service.delete_device(service.controld_device_id)
                except Exception as exc:
                    logger.error(
                        "controld_device_delete_exception",
                        service_id=service.id,
                        device_id=service.controld_device_id,
                        error=str(exc),
                    )
                    success = False

                if success:
                    logger.info("controld_device_deleted_successfully", service_id=service.id)
                else:
                    # Log the warning but DO NOT use 'continue'. We still must expire
                    # the database record so it doesn't get stuck in an active state.
                    logger.warning(
                        "failed_to_delete_controld_device_proceeding_to_expire_locally",
                        service_id=service.id,
                    )

            try:
                service.status = VPNServiceStatus.EXPIRED.value
                await session.commit()
                processed += 1
            except Exception as exc:
                await session.rollback()
                logger.error("failed_to_mark_dns_service_expired", service_id=service.id, error=str(exc))
                continue

            # Send Persian notification to user if it's an expired test account
            # Send customized expiration notifications
            if bot is not None and service.user is not None:
                try:
                    if service.is_test_account:
                        text = (
                            "⏳ اکانت تست شما منقضی شد.\n\n"
                            f"🗓 تاریخ انقضا: {format_datetime(service.expire_at)}\n"
                            "دسترسی DNS شما غیرفعال شد."
                        )
                    else:
                        text = (
                            "⏳ اشتراک DNS شما به پایان رسید.\n\n"
                            f"🗓 تاریخ انقضا: {format_datetime(service.expire_at)}\n"
                            "دسترسی شما غیرفعال شد. برای تمدید یا خرید اشتراک جدید می‌توانید از منوی اصلی اقدام کنید."
                        )
                    
                    await bot.send_message(
                        chat_id=service.user.telegram_id,
                        text=text,
                    )
                except Exception as exc:
                    logger.warning("failed_to_notify_expired_service_owner", service_id=service.id, error=str(exc))

        return processed


async def sync_plans_with_controld(session) -> None:
    """
    Synchronizes your Control D dashboard Profiles with local Plans in PostgreSQL.
    """
    settings = get_settings()
    
    # Instantiate the proxy-aware OOP client
    cd_service = ControlDService(settings)
    
    # Use the OOP client method to retrieve profiles
    profiles = await cd_service.fetch_controld_profiles()
    if not profiles:
        logger.warning("no_controld_profiles_found_or_sync_failed")
        return

    logger.info("syncing_controld_profiles_to_database", count=len(profiles))

    for profile in profiles:
        profile_id = profile["id"]
        profile_name = profile["name"]
        profile_desc = profile["description"] or "سرویس دی‌ان‌اس اختصاصی"

        # Check if this profile is already registered as a plan in our DB
        stmt = select(Plan).where(Plan.controld_profile_id == profile_id)
        result = await session.execute(stmt)
        
        # Strictly use .first() to prevent multiple-row crashes
        existing_plan = result.scalars().first()

        if existing_plan is None:
            # Create a brand new plan with default prices/durations
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
            # Update title and description if they were modified on the Control D dashboard
            existing_plan.title = profile_name
            if profile["description"]:
                existing_plan.description = profile_desc
                
    await session.commit()


async def expiration_scheduler_loop(bot: Bot | None = None, interval_seconds: int = 60) -> None:
    """
    Background loop checking for expired subscriptions on a short interval.
    """
    logger.info("starting_expiration_scheduler_loop", interval_seconds=interval_seconds)
    while True:
        try:
            processed = await cleanup_expired_dns_services(bot=bot)
            if processed:
                logger.info("expired_dns_services_processed", count=processed)
        except Exception as e:
            logger.error("expiration_scheduler_loop_error", error=str(e))
        
        await asyncio.sleep(interval_seconds)