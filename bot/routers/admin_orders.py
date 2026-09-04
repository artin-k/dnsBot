# bot/routers/admin_orders.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from html import escape
import structlog
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Order, OrderStatus, OrderKind, Payment, PaymentStatus, VPNService, VPNServiceStatus
from app.repositories.orders import OrdersRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.services import ServicesRepository
from app.repositories.subscriptions import SubscriptionsRepository
from app.services.controld import ControlDService
from app.services.payment_service import (
    PaymentAlreadyProcessedError,
    PaymentApprovalError,
    PaymentExpiredError,
    PaymentService,
)
from app.services.vpn_panel import VPNPanelService
from app.utils.formatting import format_money
from bot import texts
from bot.keyboards.admin import (
    AdminOrderCallback,
    AdminPaymentCallback,
    AdminActionCallback,
)
from bot.routers.services import create_secure_ip_update_keyboard
from bot.utils.auto_clean import schedule_message_deletion

router = Router(name="admin_orders")
logger = structlog.get_logger(__name__)


async def _get_service_for_order(session: AsyncSession, order: Order) -> VPNService | None:
    stmt = select(VPNService).where(VPNService.order_id == order.id)
    res = await session.execute(stmt)
    service = res.scalars().first()
    if service:
        return service
    stmt = (
        select(VPNService)
        .where(VPNService.user_id == order.user_id, VPNService.is_test_account == False)
        .order_by(VPNService.expire_at.desc())
        .limit(1)
    )
    res = await session.execute(stmt)
    return res.scalars().first()


@router.callback_query(AdminOrderCallback.filter())
async def admin_order_callback(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    from bot.routers.admin import _is_admin, get_controld_device_ips, _approved_message

    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
        return

    await callback.answer()
    action = callback_data.action
    order_id = callback_data.order_id

    if action == "list":
        await _show_recent_orders(callback, session, page=callback_data.page)
        return

    order = await OrdersRepository(session).get_with_details(order_id)
    if not order:
        await callback.message.answer("سفارش پیدا نشد.")
        return

    if action == "detail":
        await _show_order_detail_panel(callback, order)
        return

    payment_service = PaymentService(session, VPNPanelService(), settings)

    if action == "complete":
        if order.status == OrderStatus.COMPLETED.value:
            await callback.answer("این سفارش قبلاً تکمیل شده است.", show_alert=True)
            return
        if order.payment:
            try:
                result = await payment_service.approve_payment(order.payment.id)
                service_record = await _get_service_for_order(session, order)

                device_id = "unknown"
                ips = {"ipv4_primary": "76.76.2.162", "ipv4_secondary": "76.76.10.162"}
                if service_record:
                    device_id = service_record.controld_device_id
                    ips = await get_controld_device_ips(device_id, settings)

                keyboard = await create_secure_ip_update_keyboard(session, service_record.id) if service_record else None
                sent_msg = await callback.bot.send_message(
                    chat_id=result.user_telegram_id,
                    text=_approved_message(
                        result,
                        expire_at=service_record.expire_at if service_record else None,
                        ipv4_primary=ips["ipv4_primary"],
                        ipv4_secondary=ips["ipv4_secondary"],
                        custom_username=order.custom_username if order else None,
                    ),
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
                if sent_msg:
                    await schedule_message_deletion(callback.bot, sent_msg.chat.id, sent_msg.message_id, delay_seconds=7200)

                await callback.message.answer(f"✅ سفارش {order.tracking_code} با موفقیت تکمیل شد.")
            except Exception as e:
                await callback.message.answer(f"❌ خطا در تکمیل سفارش: {e}")
        else:
            order.status = OrderStatus.COMPLETED.value
            await session.commit()
            await callback.message.answer(f"✅ وضعیت سفارش {order.tracking_code} به تکمیل‌شده تغییر یافت.")

        order = await OrdersRepository(session).get_with_details(order_id)
        await _show_order_detail_panel(callback, order)
        return

    if action == "cancel":
        order.status = OrderStatus.EXPIRED.value
        await session.commit()
        await callback.message.answer(f"✅ سفارش {order.tracking_code} لغو شد.")
        order = await OrdersRepository(session).get_with_details(order_id)
        await _show_order_detail_panel(callback, order)
        return

    if action == "delete":
        tracking_code = order.tracking_code
        if order.payment:
            await session.delete(order.payment)
        await session.delete(order)
        await session.commit()
        await callback.message.answer(f"✅ سفارش {tracking_code} کاملاً حذف شد.")
        await _show_recent_orders(callback, session)
        return


@router.callback_query(AdminPaymentCallback.filter())
async def admin_payment_action(
    callback: CallbackQuery,
    callback_data: AdminPaymentCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    from bot.routers.admin import _is_admin, get_controld_device_ips, _approved_message

    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
        return

    payment_service = PaymentService(session, VPNPanelService(), settings)
    try:
        if callback_data.action == "approve":
            result = await payment_service.approve_payment(callback_data.payment_id)
            payment_record = await session.get(Payment, callback_data.payment_id)
            service_record = None
            device_id = "unknown"
            ips = {"ipv4_primary": "76.76.2.162", "ipv4_secondary": "76.76.10.162"}

            if payment_record and payment_record.order:
                service_record = await _get_service_for_order(session, payment_record.order)
                if service_record:
                    device_id = service_record.controld_device_id
                    ips = await get_controld_device_ips(device_id, settings)

            keyboard = await create_secure_ip_update_keyboard(session, service_record.id) if service_record else None
            sent_msg = await callback.bot.send_message(
                chat_id=result.user_telegram_id,
                text=_approved_message(
                    result,
                    expire_at=service_record.expire_at if service_record else None,
                    ipv4_primary=ips["ipv4_primary"],
                    ipv4_secondary=ips["ipv4_secondary"],
                    custom_username=payment_record.order.custom_username if payment_record and payment_record.order else None,
                ),
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            if sent_msg:
                await schedule_message_deletion(callback.bot, sent_msg.chat.id, sent_msg.message_id, delay_seconds=7200)

            await callback.answer("پرداخت تایید شد.")
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        elif callback_data.action == "reject":
            result = await payment_service.reject_payment(callback_data.payment_id)
            await callback.bot.send_message(
                chat_id=result.user_telegram_id,
                text="❌ رسید پرداخت شما تایید نشد. در صورت وجود مشکل با پشتیبانی در ارتباط باشید.",
            )
            await callback.answer("رسید رد شد.")
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
    except (PaymentExpiredError, PaymentAlreadyProcessedError, PaymentApprovalError) as exc:
        await callback.answer(f"⚠️ {str(exc)}", show_alert=True)


async def _show_recent_orders(callback: CallbackQuery, session: AsyncSession, page: int = 0) -> None:
    limit = 8
    offset = page * limit
    result = await session.execute(
        select(Order).order_by(Order.created_at.desc()).limit(limit + 1).offset(offset)
    )
    orders = list(result.scalars().all())
    has_next = len(orders) > limit
    if has_next:
        orders = orders[:limit]

    if not orders and page == 0:
        await callback.message.answer("هنوز سفارشی ثبت نشده است.")
        return

    builder = InlineKeyboardBuilder()
    for order in orders:
        emoji = "🟢" if order.status == "completed" else "🔴" if order.status in ("expired", "canceled") else "🟡"
        builder.button(
            text=f"{emoji} {order.tracking_code} | {format_money(order.amount)}ت",
            callback_data=AdminOrderCallback(action="detail", order_id=order.id),
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=AdminOrderCallback(action="list", page=page - 1).pack()))
    if has_next:
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=AdminOrderCallback(action="list", page=page + 1).pack()))
    if nav:
        builder.row(*nav)

    builder.row(InlineKeyboardButton(text="↩️ بازگشت", callback_data=AdminActionCallback(action="cat_sales").pack()))
    builder.adjust(1)

    await callback.message.edit_text(
        f"🧾 لیست سفارش‌ها (صفحه {page + 1}):",
        reply_markup=builder.as_markup(),
    )


async def _show_order_detail_panel(callback: CallbackQuery, order: Order) -> None:
    from bot.menu_actions import format_order_detail
    detail_text = format_order_detail(order)

    builder = InlineKeyboardBuilder()
    if order.status != OrderStatus.COMPLETED.value:
        builder.button(text="✅ تکمیل و تایید دستی", callback_data=AdminOrderCallback(action="complete", order_id=order.id))
        builder.button(text="🔴 لغو سفارش", callback_data=AdminOrderCallback(action="cancel", order_id=order.id))
    builder.button(text="🗑 حذف سفارش", callback_data=AdminOrderCallback(action="delete", order_id=order.id))
    builder.button(text="↩️ بازگشت به لیست", callback_data=AdminOrderCallback(action="list"))
    builder.adjust(1)

    await callback.message.edit_text(detail_text, reply_markup=builder.as_markup())