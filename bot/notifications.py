from __future__ import annotations

from html import escape

import structlog
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Order, OrderKind, Payment, WalletTransaction
from app.repositories.users import UsersRepository
from app.utils.formatting import format_money, format_order_type_fa
from bot.keyboards.admin import payment_review_keyboard
from bot.keyboards.wallet import wallet_topup_review_keyboard

logger = structlog.get_logger(__name__)


async def get_admin_ids(session: AsyncSession, settings: Settings) -> list[int]:
    admin_ids = set(settings.admin_ids)
    if settings.root_admin_telegram_id is not None:
        admin_ids.add(settings.root_admin_telegram_id)
    admin_ids.update(await UsersRepository(session).list_admin_telegram_ids())
    return sorted(admin_ids)


async def notify_admins_order_payment(
    *,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    payment: Payment,
    order: Order,
    receipt_file_id: str,
) -> int:
    caption = format_order_payment_admin_caption(order, payment)
    sent_count = 0
    for admin_id in await get_admin_ids(session, settings):
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=receipt_file_id,
                caption=caption,
                reply_markup=payment_review_keyboard(payment.id),
            )
            sent_count += 1
        except Exception as exc:
            logger.warning("admin_order_receipt_photo_failed", admin_id=admin_id, error=str(exc))
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=caption,
                    reply_markup=payment_review_keyboard(payment.id),
                )
                sent_count += 1
            except Exception as send_exc:
                logger.warning("admin_order_receipt_message_failed", admin_id=admin_id, error=str(send_exc))
    if sent_count == 0:
        logger.warning("no_admin_notified_for_order_payment", payment_id=payment.id, order_id=order.id)
    return sent_count


async def notify_admins_wallet_topup(
    *,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    transaction: WalletTransaction,
    receipt_file_id: str,
) -> int:
    caption = format_wallet_topup_admin_caption(transaction)
    sent_count = 0
    for admin_id in await get_admin_ids(session, settings):
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=receipt_file_id,
                caption=caption,
                reply_markup=wallet_topup_review_keyboard(transaction.id),
            )
            sent_count += 1
        except Exception as exc:
            logger.warning("admin_wallet_receipt_photo_failed", admin_id=admin_id, error=str(exc))
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=caption,
                    reply_markup=wallet_topup_review_keyboard(transaction.id),
                )
                sent_count += 1
            except Exception as send_exc:
                logger.warning("admin_wallet_receipt_message_failed", admin_id=admin_id, error=str(send_exc))
    if sent_count == 0:
        logger.warning("no_admin_notified_for_wallet_topup", transaction_id=transaction.id)
    return sent_count


def format_order_payment_admin_caption(order: Order, payment: Payment) -> str:
    user = order.user
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    service_username = order.custom_username or "-"
    if order.renewal_service:
        service_username = order.renewal_service.username
    receipt_status = "رسید دریافت شده" if payment.receipt_file_id else "بدون رسید"
    inventory_line = ""
    if order.order_kind == OrderKind.PURCHASE.value:
        inventory_line = f"\n📦 کانفیگ رزرو شده: {'بله' if order.config_inventory_id else 'خیر'} | شناسه: {order.config_inventory_id or '-'}"
    return f"""🧾 پرداخت جدید در انتظار تایید

👤 کاربر: {escape(user.first_name or "-")}
🔗 یوزرنیم تلگرام: {escape(username)}
🆔 آیدی عددی: {user.telegram_id}
📱 موبایل: {escape(user.phone_number or "-")}
🛒 کد پیگیری: {order.tracking_code}
⚡ نوع سفارش: {format_order_type_fa(order.order_kind)}
⚡ پلن: {escape(order.plan.title if order.plan else "-")}
💵 مبلغ: {format_money(order.amount)} تومان
🔐 نام کاربری/سرویس: {escape(service_username)}
📎 وضعیت رسید: {receipt_status}{inventory_line}"""


def format_wallet_topup_admin_caption(transaction: WalletTransaction) -> str:
    user = transaction.user
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    return f"""🏦 شارژ کیف پول جدید در انتظار تایید

👤 کاربر: {escape(user.first_name or "-")}
🔗 یوزرنیم تلگرام: {escape(username)}
🆔 آیدی عددی: {user.telegram_id}
📱 موبایل: {escape(user.phone_number or "-")}
💵 مبلغ: {format_money(transaction.amount)} تومان"""
