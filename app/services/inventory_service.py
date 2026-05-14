from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import get_settings
from app.models import ConfigInventory, ConfigInventoryStatus, Order, OrderKind, OrderStatus, PaymentStatus, Plan
from app.repositories.config_inventory import ConfigInventoryRepository
from app.repositories.users import UsersRepository

logger = structlog.get_logger(__name__)

VALID_CONFIG_PREFIXES = (
    "vless://",
    "vmess://",
    "trojan://",
    "ss://",
    "hysteria://",
    "hy2://",
    "tuic://",
    "http://",
    "https://",
)


class InventoryUnavailableError(RuntimeError):
    pass


class ConfigInventoryValidationError(ValueError):
    pass


async def get_available_count(session: AsyncSession, plan_id: int) -> int:
    return await ConfigInventoryRepository(session).count_by_status(plan_id, ConfigInventoryStatus.AVAILABLE.value)


async def reserve_config_for_order(session: AsyncSession, plan_id: int, order_id: int) -> ConfigInventory | None:
    order = await session.get(Order, order_id, with_for_update=True)
    if order is None:
        return None
    if order.config_inventory_id:
        existing = await session.get(ConfigInventory, order.config_inventory_id, with_for_update=True)
        if existing is not None and existing.status == ConfigInventoryStatus.RESERVED.value:
            return existing

    repo = ConfigInventoryRepository(session)
    item = await repo.get_available_for_update(plan_id)
    if item is None:
        return None

    now = datetime.now(timezone.utc)
    item.status = ConfigInventoryStatus.RESERVED.value
    item.reserved_by_order_id = order.id
    item.reserved_at = now
    item.sold_to_user_id = None
    item.sold_at = None
    order.config_inventory_id = item.id
    await session.flush()
    return item


async def release_reserved_config(session: AsyncSession, order_id: int) -> None:
    order = await session.get(Order, order_id, with_for_update=True)
    item: ConfigInventory | None = None
    if order and order.config_inventory_id:
        item = await session.get(ConfigInventory, order.config_inventory_id, with_for_update=True)
    if item is None:
        item = await ConfigInventoryRepository(session).get_reserved_for_order(order_id)
    if item is None or item.status != ConfigInventoryStatus.RESERVED.value:
        return
    item.status = ConfigInventoryStatus.AVAILABLE.value
    item.reserved_by_order_id = None
    item.reserved_at = None
    if order is not None:
        order.config_inventory_id = None
    await session.flush()


async def mark_config_sold(session: AsyncSession, order_id: int, user_id: int) -> ConfigInventory:
    order = await session.get(Order, order_id, with_for_update=True)
    if order is None:
        raise InventoryUnavailableError("Order not found")

    item: ConfigInventory | None = None
    if order.config_inventory_id:
        item = await session.get(ConfigInventory, order.config_inventory_id, with_for_update=True)
    if item is None:
        item = await reserve_config_for_order(session, order.plan_id, order.id)
    if item is None or item.status != ConfigInventoryStatus.RESERVED.value:
        raise InventoryUnavailableError("No reserved config inventory")

    now = datetime.now(timezone.utc)
    item.status = ConfigInventoryStatus.SOLD.value
    item.sold_to_user_id = user_id
    item.sold_at = now
    item.reserved_by_order_id = order.id
    order.config_inventory_id = item.id
    await session.flush()
    return item


async def release_expired_reservations(session: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    result = await session.scalars(
        select(Order)
        .options(joinedload(Order.payment))
        .where(
            Order.order_kind == OrderKind.PURCHASE.value,
            Order.status == OrderStatus.PENDING_PAYMENT.value,
            Order.expires_at.is_not(None),
            Order.expires_at < now,
        )
        .with_for_update()
    )
    expired_orders = list(result.unique().all())
    for order in expired_orders:
        order.status = OrderStatus.EXPIRED.value
        if order.payment and order.payment.status == PaymentStatus.PENDING.value:
            order.payment.status = PaymentStatus.EXPIRED.value
        await release_reserved_config(session, order.id)
    if expired_orders:
        await session.flush()
    return len(expired_orders)


async def notify_admins_low_or_empty_inventory(bot, session: AsyncSession, plan_id: int) -> None:
    settings = get_settings()
    plan = await session.get(Plan, plan_id)
    if plan is None:
        return
    available_count = await get_available_count(session, plan_id)
    if available_count <= 0:
        text = f"""🚨 موجودی تعرفه به پایان رسید

تعرفه: {plan.title}
شناسه تعرفه: {plan.id}

فروش این تعرفه تا زمان شارژ موجودی متوقف می‌شود."""
    elif available_count <= settings.config_low_stock_threshold:
        text = f"""⚠️ موجودی تعرفه کم است

تعرفه: {plan.title}
موجودی باقی‌مانده: {available_count}"""
    else:
        return

    for admin_id in await _get_inventory_admin_ids(session):
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception as exc:
            logger.warning("inventory_admin_notification_failed", admin_id=admin_id, plan_id=plan_id, error=str(exc))


async def notify_admins_empty_inventory_attempt(bot, session: AsyncSession, plan: Plan) -> None:
    text = f"""⚠️ موجودی تعرفه به پایان رسیده است

تعرفه: {plan.title}
شناسه تعرفه: {plan.id}

لطفاً از پنل مدیریت موجودی کانفیگ‌ها را شارژ کنید."""
    for admin_id in await _get_inventory_admin_ids(session):
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception as exc:
            logger.warning("inventory_empty_attempt_notification_failed", admin_id=admin_id, plan_id=plan.id, error=str(exc))


async def _get_inventory_admin_ids(session: AsyncSession) -> list[int]:
    settings = get_settings()
    admin_ids = set(settings.admin_ids)
    if settings.root_admin_telegram_id is not None:
        admin_ids.add(settings.root_admin_telegram_id)
    admin_ids.update(await UsersRepository(session).list_admin_telegram_ids())
    return sorted(admin_ids)


def normalize_inventory_link(value: str | None, *, required: bool = False) -> str | None:
    text = (value or "").strip()
    if text == "-":
        text = ""
    if required and not text:
        raise ConfigInventoryValidationError("لینک کانفیگ نمی‌تواند خالی باشد.")
    if not text:
        return None
    if not text.lower().startswith(VALID_CONFIG_PREFIXES):
        raise ConfigInventoryValidationError("فرمت لینک کانفیگ معتبر نیست.")
    return text
