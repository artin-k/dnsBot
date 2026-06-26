# bot/routers/tariffs.py
from html import escape

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.plans import PlansRepository
# Fix: Import the standard format_money utility used across the project
from app.utils.formatting import format_money
from bot import texts

router = Router(name="tariffs")


def format_duration_fa(hours: int) -> str:
    """
    Dynamically formats hours into readable Persian text.
    Shows days if divisible by 24, otherwise displays hours.
    """
    if hours >= 24 and hours % 24 == 0:
        days = hours // 24
        return f"{days} روز"
    return f"{hours} ساعت"


@router.message(F.text == texts.BTN_TARIFFS)
async def tariffs(message: Message, session: AsyncSession) -> None:
    # 1. Fetch active DNS plans
    plans = await PlansRepository(session).list_active()
    if not plans:
        await message.answer("در حال حاضر تعرفه فعالی ثبت نشده است.")
        return

    lines = ["💰 تعرفه اشتراک‌های DNS"]
    for index, plan in enumerate(plans, start=1):
        # 2. Rebranded to support dynamic, unlimited DNS provisioning (Always Available)
        stock_status = "✅ وضعیت: فعال و آماده تحویل"
        
        # Safely convert duration_hours to a readable string (e.g. 720 hours -> 30 روز)
        duration_text = format_duration_fa(plan.duration_hours or 0)
        
        lines.append(
            f"""
{index}. {escape(plan.title)}
🗓 مدت اعتبار: {duration_text}
💵 قیمت: {format_money(plan.price)} تومان
{stock_status}"""
        )
        if plan.description:
            lines.append(f"📝 توضیحات: {escape(plan.description)}")

    lines.append("\nبرای خرید، از گزینه «🔐 خرید اشتراک DNS» استفاده کنید.")
    await message.answer("\n".join(lines))