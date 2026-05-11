from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.services import ServicesRepository
from app.repositories.users import UsersRepository
from bot import menu_actions
from bot import texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.services import ServiceActionCallback, services_actions_keyboard

router = Router(name="services")


@router.message(F.text == texts.BTN_MY_SERVICES)
async def my_services(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await menu_actions.show_my_services(message, session)


@router.callback_query(ServiceActionCallback.filter(F.action.in_({"link", "status"})))
async def service_action(
    callback: CallbackQuery,
    callback_data: ServiceActionCallback,
    session: AsyncSession,
) -> None:
    await callback.answer()
    if callback.from_user is None:
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        await _safe_answer(callback, "ابتدا /start را ارسال کنید.")
        return

    service = await ServicesRepository(session).get_user_service(callback_data.service_id, user.id)
    if service is None:
        await _safe_answer(callback, "این سرویس پیدا نشد یا متعلق به حساب شما نیست.")
        return

    if callback_data.action == "link":
        await _safe_answer(
            callback,
            f"""🔗 لینک‌های سرویس {escape(service.username)}

لینک اشتراک:
{escape(service.subscription_link or "ثبت نشده")}

لینک کانفیگ:
{escape(service.config_link or "ثبت نشده")}""",
        )
        return

    await _safe_answer(callback, menu_actions.format_service_summary(service))


async def _safe_answer(callback: CallbackQuery, text: str) -> None:
    if callback.message:
        try:
            await callback.message.edit_text(text)
        except Exception:
            await callback.message.answer(text)
