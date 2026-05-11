from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import OrderStatus
from app.repositories.orders import OrdersRepository
from app.repositories.users import UsersRepository
from app.services.order_service import OrderService
from bot import menu_actions, texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.tracking import OrderDetailCallback, OrderSearchCallback
from bot.routers.menu import handle_main_menu_text
from bot.states.tracking import TrackingStates

router = Router(name="tracking")


@router.message(F.text == texts.BTN_TRACK_ORDER)
async def show_tracking_orders(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await state.clear()
    await menu_actions.show_order_tracking(message, session, settings)


@router.callback_query(OrderSearchCallback.filter())
async def ask_tracking_code(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(TrackingStates.waiting_code)
    if callback.message:
        await callback.message.answer("لطفاً کد پیگیری سفارش خود را ارسال کنید:")


@router.callback_query(OrderDetailCallback.filter())
async def order_detail(
    callback: CallbackQuery,
    callback_data: OrderDetailCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if callback.from_user is None:
        await callback.answer("این درخواست دیگر معتبر نیست.", show_alert=True)
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    order = await OrdersRepository(session).get_with_details(callback_data.order_id)
    if user is None or order is None or order.user_id != user.id:
        await callback.answer("این درخواست دیگر معتبر نیست.", show_alert=True)
        return

    if order.status == OrderStatus.PENDING_PAYMENT.value:
        await OrderService(session, settings).expire_order_if_unpaid(order)

    await callback.answer()
    await _safe_edit_or_answer(callback, menu_actions.format_order_detail(order))


@router.message(TrackingStates.waiting_code, F.text)
async def receive_tracking_code(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await handle_main_menu_text(message, state, session, settings):
        return
    if message.from_user is None or not message.text:
        return

    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id)
    if user is None:
        await state.clear()
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return

    tracking_code = message.text.strip()
    order = await OrdersRepository(session).get_by_tracking_code_for_user(tracking_code, user.id)
    if order is None:
        await message.answer("❌ سفارشی با این کد پیگیری برای حساب شما پیدا نشد.")
        return

    if order.status == OrderStatus.PENDING_PAYMENT.value:
        await OrderService(session, settings).expire_order_if_unpaid(order)

    await state.clear()
    await message.answer(menu_actions.format_order_detail(order), reply_markup=main_menu_keyboard())


@router.message(TrackingStates.waiting_code)
async def receive_invalid_tracking_code(message: Message) -> None:
    await message.answer("لطفاً کد پیگیری سفارش را به صورت متن ارسال کنید.")


async def _safe_edit_or_answer(callback: CallbackQuery, text: str) -> None:
    if callback.message:
        try:
            await callback.message.edit_text(text)
        except Exception:
            await callback.message.answer(text)
