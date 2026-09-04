# bot/routers/admin_plans.py
from __future__ import annotations

from html import escape
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.repositories.plans import PlansRepository
from app.utils.formatting import format_money
from bot.keyboards.admin import (
    AdminPlanCallback,
    AdminActionCallback,
    plan_detail_keyboard,
    plans_management_keyboard,
    add_plan_confirm_keyboard,
    plan_delete_confirm_keyboard,
)
from bot.states.admin import AdminAddPlanStates, AdminEditPlanStates

router = Router(name="admin_plans")

EDIT_FIELD_MAP = {
    "edit_title": ("title", "عنوان جدید تعرفه را ارسال کنید:", "title"),
    "edit_desc": ("description", "توضیحات جدید را ارسال کنید (برای خالی کردن، - بفرستید):", "description"),
    "edit_duration": ("duration_hours", "مدت جدید را به ساعت ارسال کنید (مثال: 720 برای ۳۰ روز):", "positive_int"),
    "edit_price": ("price", "قیمت جدید را به تومان ارسال کنید:", "positive_int"),
    "edit_sort": ("sort_order", "ترتیب نمایش جدید را ارسال کنید:", "int"),
}


def _format_plan_detail(plan) -> str:
    status = "🟢 فعال" if plan.is_active else "🔴 غیرفعال"
    desc = plan.description or "-"
    hours = plan.duration_hours or 720
    duration_text = f"{hours // 24} روز" if hours >= 24 and hours % 24 == 0 else f"{hours} ساعت"

    return f"""📦 <b>جزئیات تعرفه</b>

🆔 شناسه: {plan.id}
📌 وضعیت: {status}
⚡ عنوان: {escape(plan.title)}
📝 توضیحات: {escape(desc)}
🗓 مدت: {duration_text}
💵 قیمت: {format_money(plan.price)} تومان
🔢 ترتیب: {plan.sort_order}"""


@router.callback_query(AdminPlanCallback.filter())
async def admin_plan_action(
    callback: CallbackQuery,
    callback_data: AdminPlanCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    from bot.routers.admin import _is_admin

    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ عدم دسترسی.", show_alert=True)
        return

    await callback.answer()
    repo = PlansRepository(session)
    plan = await repo.get(callback_data.plan_id)
    if not plan:
        await callback.message.answer("تعرفه پیدا نشد.")
        return

    action = callback_data.action
    if action == "detail":
        await callback.message.edit_text(_format_plan_detail(plan), reply_markup=plan_detail_keyboard(plan), parse_mode="HTML")
        return

    if action in EDIT_FIELD_MAP:
        field, prompt, validator = EDIT_FIELD_MAP[action]
        await state.set_state(AdminEditPlanStates.value)
        await state.update_data(plan_id=plan.id, field=field, validator=validator)
        await callback.message.answer(prompt)
        return

    if action == "toggle":
        await repo.set_active(plan.id, not plan.is_active)
        await session.commit()
        refreshed = await repo.get(plan.id)
        await callback.message.edit_text(_format_plan_detail(refreshed), reply_markup=plan_detail_keyboard(refreshed), parse_mode="HTML")
        return

    if action == "delete":
        await callback.message.edit_text(
            f"⚠️ آیا از حذف تعرفه <b>{escape(plan.title)}</b> مطمئن هستید؟",
            reply_markup=plan_delete_confirm_keyboard(plan),
            parse_mode="HTML",
        )
        return

    if action == "delete_confirm":
        await repo.delete(plan.id)
        await session.commit()
        await callback.message.answer("✅ تعرفه حذف شد.")
        plans = await repo.list_all()
        await callback.message.answer("📦 مدیریت تعرفه‌ها:", reply_markup=plans_management_keyboard(plans))


# --- Add Plan FSM ---
@router.message(AdminAddPlanStates.title)
async def fsm_add_plan_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("عنوان معتبر نیست.")
        return
    await state.update_data(title=title)
    await state.set_state(AdminAddPlanStates.description)
    await message.answer("توضیحات تعرفه را وارد کنید (یا - را بفرستید):")


@router.message(AdminAddPlanStates.description)
async def fsm_add_plan_desc(message: Message, state: FSMContext) -> None:
    desc = message.text.strip()
    await state.update_data(description=None if desc == "-" else desc)
    await state.set_state(AdminAddPlanStates.duration_days)
    await message.answer("مدت زمان اعتبار را به ساعت ارسال کنید (مثال: 720 برای ۳۰ روز):")


@router.message(AdminAddPlanStates.duration_days)
async def fsm_add_plan_hours(message: Message, state: FSMContext) -> None:
    if not message.text or not message.text.isdigit():
        await message.answer("لطفاً یک عدد صحیح ارسال کنید.")
        return
    await state.update_data(duration_hours=int(message.text))
    await state.set_state(AdminAddPlanStates.price)
    await message.answer("قیمت تعرفه را به تومان ارسال کنید (مثال: 50000):")


@router.message(AdminAddPlanStates.price)
async def fsm_add_plan_price(message: Message, state: FSMContext) -> None:
    digits = (message.text or "").replace(",", "").strip()
    if not digits.isdigit():
        await message.answer("لطفاً قیمت معتبر به تومان وارد کنید.")
        return
    await state.update_data(price=int(digits), sort_order=0)
    data = await state.get_data()
    await state.set_state(AdminAddPlanStates.confirm)
    await message.answer(
        f"⚡ <b>تایید تعرفه جدید:</b>\n\n"
        f"عنوان: {escape(data['title'])}\n"
        f"مدت: {data['duration_hours']} ساعت\n"
        f"قیمت: {format_money(data['price'])} تومان",
        reply_markup=add_plan_confirm_keyboard(),
        parse_mode="HTML",
    )