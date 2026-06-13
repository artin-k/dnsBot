# # bot/routers/controld_buy.py
# import secrets
# import httpx
# import re
# from datetime import datetime, timezone, timedelta
# from html import escape
# import structlog
# from aiogram import F, Router
# from aiogram.filters import StateFilter
# from aiogram.fsm.context import FSMContext
# from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
# from aiogram.utils.keyboard import InlineKeyboardBuilder
# from sqlalchemy import select
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.config import Settings
# from app.models import Plan, VPNService
# from app.repositories.plans import PlansRepository
# from app.repositories.users import UsersRepository
# from app.services.controld import create_dns_device, ControlDService
# from bot import texts

# # FSM state import
# from bot.states.buy import BuyStates

# logger = structlog.get_logger(__name__)
# router = Router(name="controld_buy")

# # ============================================================================
# # CONFIGURATION
# # ============================================================================
# # For local testing, paste your active ngrok URL here (WITHOUT trailing slash /).
# # Example: "https://your-ngrok-subdomain.ngrok-free.app"
# # When moving to VPS production, replace this with your VPS domain or IP:
# # Example: "http://82.115.24.241:8000"
# WEB_SERVER_BASE_URL = "https://your-ngrok-subdomain.ngrok-free.app"


# def _get_ip_registration_keyboard(device_id: str) -> InlineKeyboardMarkup:
#     """
#     Generates inline keyboard matching your target design [1].
#     """
#     builder = InlineKeyboardBuilder()
#     builder.button(text="✳️ ثبت آی‌پی اتوماتیک ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
#     builder.button(text="✳️ ثبت آی‌پی اتوماتیک 2 ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
#     builder.button(text="🤖 ثبت آی‌پی دستی 🤖", callback_data=f"manual_ip_reg:{device_id}")
#     builder.adjust(1)
#     return builder.as_markup()


# # ============================================================================
# # TASK 1: MAIN PLANS MENU (With Custom Formatting)
# # ============================================================================

# @router.message(F.text == texts.BTN_BUY)
# @router.callback_query(F.data == "menu:buy", StateFilter("*"))
# async def show_dns_plans_menu(
#     event: Message | CallbackQuery,
#     state: FSMContext,
#     session: AsyncSession,
#     settings: Settings,
# ) -> None:
#     user_id = event.from_user.id if event.from_user else 0
#     user = await UsersRepository(session).get_by_telegram_id(user_id) if user_id else None
    
#     # Enforce Phone Verification
#     if user is None or not user.is_phone_verified:
#         from bot.keyboards.verification import phone_verification_keyboard
#         from bot.states.wallet import VerificationStates
#         await state.set_state(VerificationStates.waiting_contact)
#         await state.update_data(next_section="buy")
        
#         prompt_text = "⚠️ برای خرید اشتراک DNS، ابتدا باید شماره موبایل خود را تایید کنید.\n\nلطفاً دکمه زیر را بزنید تا شماره تماس شما ارسال شود 👇"
#         if isinstance(event, CallbackQuery):
#             await event.answer()
#             await event.message.answer(prompt_text, reply_markup=phone_verification_keyboard())
#         else:
#             await event.answer(prompt_text, reply_markup=phone_verification_keyboard())
#         return

#     if isinstance(event, CallbackQuery):
#         await event.answer()

#     # Query plans
#     plans = await PlansRepository(session).list_active()
#     if not plans:
#         msg = "در حال حاضر پلن فعالی برای خرید وجود ندارد."
#         if isinstance(event, CallbackQuery):
#             await event.message.answer(msg)
#         else:
#             await event.answer(msg)
#         return

#     builder = InlineKeyboardBuilder()
#     for plan in plans:
#         formatted_price = f"{plan.price:,}"
#         builder.button(
#             text=f"🔹 {plan.title} - {formatted_price} تومان 🔹",
#             callback_data=f"buy_plan:{plan.id}"
#         )
        
#     builder.button(text="🎁 دریافت اکانت تست (۲ ساعته) 🆓", callback_data="get_test_account")
#     builder.button(text=texts.BTN_BACK, callback_data="menu:main")
#     builder.adjust(1)
    
#     main_text = (
#         "لطفا یکی از پلن‌های زیر را انتخاب کنید:\n\n"
#         "در صورتی که قبلا یک پلن فعال داشته باشید و پلن جدید خریداری کنید ، "
#         "مدت زمان پلن جدید به پلن قبلی شما اضافه خواهد شد\n\n"
#         "در صورت تمدید پلن، بخاطر انتخاب مجدد شما 10 درصد تخفیف بصورت دائمی "
#         "بصورت اتوماتیک برای شما در نظر گرفته می‌شود!"
#     )
    
#     if isinstance(event, CallbackQuery):
#         await event.message.answer(main_text, reply_markup=builder.as_markup(), parse_mode="HTML")
#     else:
#         await event.answer(main_text, reply_markup=builder.as_markup(), parse_mode="HTML")


# # ============================================================================
# # TASK 2: BUY PLAN WITH REAL WALLET VALIDATION, ACCUMULATION & AUTO-DISCOUNTS
# # ============================================================================

# @router.callback_query(F.data.startswith("buy_plan:"), StateFilter("*"))
# async def handle_buy_plan(
#     callback: CallbackQuery,
#     state: FSMContext,
#     session: AsyncSession,
#     settings: Settings,
# ) -> None:
#     await callback.answer()
    
#     if callback.message is None or callback.from_user is None:
#         return

#     try:
#         plan_id = int(callback.data.split(":")[1])
#     except (ValueError, IndexError):
#         await callback.message.answer("❌ اطلاعات درخواست معتبر نیست.")
#         return

#     stmt = select(Plan).where(Plan.id == plan_id)
#     result = await session.execute(stmt)
#     plan = result.scalars().first()

#     if plan is None:
#         await callback.message.answer("❌ طرح مورد نظر پیدا نشد.")
#         return

#     # Fetch real user model directly from db
#     user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
#     if user is None:
#         await callback.message.answer("❌ حساب کاربری شما پیدا نشد.")
#         return

#     # Check if the user already has an active DNS subscription to apply renewals / discounts
#     active_stmt = select(VPNService).where(
#         VPNService.user_id == user.id,
#         VPNService.status == "active"
#     )
#     active_result = await session.execute(active_stmt)
#     current_sub = active_result.scalars().first()

#     # Calculate real price applying the automatic 10% discount for renewals
#     final_price = plan.price
#     if current_sub is not None:
#         discount_amount = int(plan.price * 0.1)
#         final_price = plan.price - discount_amount

#     # Actual database wallet balance verification [1]
#     if user.wallet_balance < final_price:
#         await callback.message.answer(
#             f"❌ موجودی کیف پول شما کافی نیست.\n\n"
#             f"💵 قیمت طرح: {final_price:,} تومان\n"
#             f"🏦 موجودی کیف پول شما: {user.wallet_balance:,} تومان\n\n"
#             f"لطفاً از منوی اصلی کیف پول خود را شارژ کنید."
#         )
#         return

#     await callback.message.answer("⚙️ در حال پردازش تراکنش و فعال‌سازی اشتراک دی‌ان‌اس...")

#     now = datetime.now(timezone.utc)

#     if current_sub is None:
#         # SCENARIO 1: NO ACTIVE SUB EXISTS - Create new device
#         expire_at = now + timedelta(hours=plan.duration_hours)
#         random_hex = secrets.token_hex(4)
#         unique_device_name = f"tg_user_{user.telegram_id}_{random_hex}"

#         # Provision device via Control D
#         device_data = await create_dns_device(
#             tg_user_id=user.telegram_id,
#             profile_id=plan.controld_profile_id,
#             duration_hours=plan.duration_hours,
#             device_type="mobile",
#             device_name=unique_device_name
#         )

#         if device_data is None:
#             await callback.message.answer("❌ خطا در برقراری ارتباط با سرورهای دی‌ان‌اس.")
#             return

#         device_id = device_data["device_id"]
#         ipv4_primary = device_data["ipv4_primary"]
#         ipv4_secondary = device_data["ipv4_secondary"]

#         # Create Subscription record
#         new_subscription = VPNService(
#             user_id=user.id,
#             plan_id=plan.id,
#             controld_device_id=device_id,
#             config_link=device_data["doh"],
#             subscription_link=device_data["dot"],
#             username=unique_device_name,
#             expire_at=expire_at,
#             status="active"
#         )
#         session.add(new_subscription)
        
#     else:
#         # SCENARIO 2: ACTIVE SUB EXISTS - Accumulate time and update existing device
#         current_expire = current_sub.expire_at
#         if current_expire.tzinfo is None:
#             current_expire = current_expire.replace(tzinfo=timezone.utc)

#         expire_at = current_expire + timedelta(hours=plan.duration_hours)
#         current_sub.expire_at = expire_at
#         current_sub.plan_id = plan.id

#         # Call ControlD API to update existing device's disable_ttl
#         new_disable_ttl = int(expire_at.timestamp())
#         controld_service = ControlDService(settings)
        
#         success = await controld_service.update_device(
#             device_id=current_sub.controld_device_id,
#             disable_ttl=new_disable_ttl
#         )

#         if not success:
#             await callback.message.answer("❌ خطا در تمدید اشتراک در سرورهای Control D.")
#             return

#         device_id = current_sub.controld_device_id
        
#         # Pull cached IPs from subscription details (or fallback to profile standards)
#         ipv4_primary = getattr(current_sub, "ipv4_primary", "ثبت شده")
#         ipv4_secondary = getattr(current_sub, "ipv4_secondary", "ثبت شده")

#     # Atomic database balance deduction [1]
#     user.wallet_balance -= final_price
#     await session.commit()
#     await state.clear()

#     # Format Expiration Timestamp using Jalali/Shamsi safely
#     try:
#         import jdatetime
#         from zoneinfo import ZoneInfo
#         tehran_tz = ZoneInfo("Asia/Tehran")
#         tehran_expire = expire_at.astimezone(tehran_tz)
#         shamsi_expire = jdatetime.datetime.fromgregorian(datetime=tehran_expire)
#         expire_str = shamsi_expire.strftime("%Y/%m/%d - %H:%M:%S")
#     except ImportError:
#         from zoneinfo import ZoneInfo
#         tehran_tz = ZoneInfo("Asia/Tehran")
#         expire_str = expire_at.astimezone(tehran_tz).strftime("%Y-%m-%d %H:%M:%S")

#     # Success Card Message Matching your design target [1]
#     success_text = f"""🔹 تاریخ انقضاء پلن : {expire_str}
# 🔷 زمان باقی‌مانده: {plan.duration_hours} ساعت
# دی ان اس اختصاصی شما :

# 🔷 Primary : <code>{ipv4_primary}</code>
# 🔷 Secondary : <code>{ipv4_secondary}</code>


# مراحل ثبت آی‌پی :
# 1️⃣ : در ابتدا گوشی موبایل و کنسول بازی رو به یک اینترنت مشترک وصل کنید .
# 2️⃣ : بدون فیلتر شکن روی دکمه ثبت آی‌پی زیر کلیک کنید.
# ❌ در صورت عدم ثبت آی‌پی DNS ها برای شما متصل نخواهد شد ❌

# : مخصوص موبایل DNS
# 🔷 Primary : <code>2.189.86.93</code>
# 🔷 Secondary : <code>2.189.86.94</code>

# توجه : اگر لینک ثبت آی‌پی اتوماتیک برای شما باز نشد ، از لینک ثبت آی‌پی اتوماتیک 2 استفاده نمایید"""
    
#     await callback.message.answer(
#         success_text, 
#         reply_markup=_get_ip_registration_keyboard(device_id), 
#         parse_mode="HTML"
#     )


# # ============================================================================
# # TASK 3: THE TEST ACCOUNT (FREE TRIAL) FLOW
# # ============================================================================

# @router.callback_query(F.data == "get_test_account", StateFilter("*"))
# async def handle_get_test_account(
#     callback: CallbackQuery,
#     state: FSMContext,
#     session: AsyncSession,
#     settings: Settings,
# ) -> None:
#     if callback.message is None or callback.from_user is None:
#         return

#     # 1. Fetch user safely from database
#     user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
#     if user is None:
#         await callback.message.answer("ابتدا /start را ارسال کنید.")
#         await callback.answer()
#         return

#     # 2. Strict Anti-Abuse DB Check [1]
#     stmt = select(VPNService).where(
#         VPNService.user_id == user.id,
#         VPNService.is_test_account == True
#     )
#     result = await session.execute(stmt)
#     existing_test = result.scalars().first()

#     if existing_test is not None:
#         await callback.answer("❌ شما قبلا از اکانت تست استفاده کرده‌اید.", show_alert=True)
#         return

#     await callback.answer()

#     # 3. Retrieve default trial profile ID from settings
#     profile_id = settings.controld_profile_id
#     if not profile_id:
#         await callback.message.answer("❌ تنظیمات اکانت تست از طرف مدیریت کامل نیست.")
#         return

#     await callback.message.answer("⚙️ در حال ساخت دی‌ان‌اس تست ۲ ساعته شما...")

#     # Generate unique device identifier [1]
#     random_hex = secrets.token_hex(4)
#     unique_device_name = f"tg_test_{user.telegram_id}_{random_hex}"

#     # 4. Call Control D API
#     controld_service = ControlDService(settings)
#     device_data = await controld_service.create_dns_device(
#         tg_user_id=user.telegram_id,
#         profile_id=profile_id,
#         duration_hours=2,
#         device_name=unique_device_name
#     )

#     if device_data is None:
#         await callback.message.answer("❌ خطا در برقراری ارتباط با سرورهای Control D. لطفاً مجدداً تلاش کنید.")
#         return

#     now = datetime.now(timezone.utc)
#     expire_at = now + timedelta(hours=2)

#     # 5. Save the test Subscription to PostgreSQL [1]
#     new_test_sub = VPNService(
#         user_id=user.id,
#         plan_id=None,
#         controld_device_id=device_data["device_id"],
#         config_link=device_data["doh"],
#         subscription_link=device_data["dot"],
#         username=unique_device_name,
#         expire_at=expire_at,
#         status="active",
#         is_test_account=True
#     )
#     session.add(new_test_sub)
#     await session.commit()
#     await state.clear()

#     # Format Shamsi Expiry String
#     try:
#         import jdatetime
#         from zoneinfo import ZoneInfo
#         tehran_tz = ZoneInfo("Asia/Tehran")
#         tehran_expire = expire_at.astimezone(tehran_tz)
#         shamsi_expire = jdatetime.datetime.fromgregorian(datetime=tehran_expire)
#         expire_str = shamsi_expire.strftime("%Y/%m/%d - %H:%M:%S")
#     except ImportError:
#         from zoneinfo import ZoneInfo
#         tehran_tz = ZoneInfo("Asia/Tehran")
#         expire_str = expire_at.astimezone(tehran_tz).strftime("%Y-%m-%d %H:%M:%S")

#     # Format matching the test instructions
#     success_text = f"""🔹 تاریخ انقضاء پلن : {expire_str}
# 🔷 زمان باقی‌مانده: 2 ساعت
# دی ان اس اختصاصی شما :

# 🔷 Primary : <code>{device_data['ipv4_primary']}</code>
# 🔷 Secondary : <code>{device_data['ipv4_secondary']}</code>


# مراحل ثبت آی‌پی :
# 1️⃣ : در ابتدا گوشی موبایل و کنسول بازی رو به یک اینترنت مشترک وصل کنید .
# 2️⃣ : بدون فیلتر شکن روی دکمه ثبت آی‌پی زیر کلیک کنید.
# ❌ در صورت عدم ثبت آی‌پی DNS ها برای شما متصل نخواهد شد ❌

# : مخصوص موبایل DNS
# 🔷 Primary : <code>2.189.86.93</code>
# 🔷 Secondary : <code>2.189.86.94</code>

# توجه : اگر لینک ثبت آی‌پی اتوماتیک برای شما باز نشد ، از لینک ثبت آی‌پی اتوماتیک 2 استفاده نمایید"""

#     await callback.message.answer(
#         success_text, 
#         reply_markup=_get_ip_registration_keyboard(device_data["device_id"]), 
#         parse_mode="HTML"
#     )


# # ============================================================================
# # TASK 4: MANUAL IP REGISTRATION FSM FLOW
# # ============================================================================

# @router.callback_query(F.data.startswith("manual_ip_reg:"), StateFilter("*"))
# async def handle_manual_ip_callback(callback: CallbackQuery, state: FSMContext) -> None:
#     """
#     Enters the FSM state asking for manual IP input.
#     """
#     await callback.answer()
#     device_id = callback.data.split(":")[1]
    
#     await state.set_state(BuyStates.waiting_manual_ip)
#     await state.update_data(device_id=device_id)
    
#     await callback.message.answer(
#         "🤖 لطفاً آی‌پی خارجی خود (IPv4) را بدون فیلترشکن وارد کنید.\n\n"
#         "مثال: `5.200.12.1`"
#     )


# @router.message(BuyStates.waiting_manual_ip, F.text)
# async def process_manual_ip(
#     message: Message, 
#     state: FSMContext, 
#     session: AsyncSession, 
#     settings: Settings
# ) -> None:
#     user_ip = message.text.strip()
    
#     # Strict IPv4 regex check
#     if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", user_ip):
#         await message.answer("❌ فرمت آی‌پی نامعتبر است. لطفاً یک آی‌پی عددی معتبر ارسال کنید.")
#         return

#     data = await state.get_data()
#     device_id = data.get("device_id")
#     if not device_id:
#         await state.clear()
#         await message.answer("❌ خطای سیستمی. لطفاً مجدداً تلاش کنید.")
#         return

#     # Authorized manual IP in Control D
#     url = f"https://api.controld.com/devices/{device_id}/ips"
#     headers = {
#         "Authorization": f"Bearer {settings.controld_api_token}",
#         "Content-Type": "application/json",
#         "accept": "application/json"
#     }
#     payload = {"ip": user_ip}

#     async with httpx.AsyncClient() as client:
#         try:
#             response = await client.post(url, json=payload, headers=headers, timeout=10.0)
#             if response.status_code in (200, 201):
#                 await state.clear()
#                 await message.answer(f"✅ آی‌پی <code>{user_ip}</code> با موفقیت به صورت دستی برای دستگاه شما ثبت شد.", parse_mode="HTML")
#             else:
#                 await message.answer(f"❌ خطا در ثبت آی‌پی در سیستم Control D:\n<code>{response.text}</code>", parse_mode="HTML")
#         except Exception as e:
#             await message.answer(f"❌ خطا در ارتباط با پنل Control D: {str(e)}")