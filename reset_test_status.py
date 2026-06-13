# reset_test_status.py
import asyncio
from sqlalchemy import delete, select
from app.database import async_session_maker
from app.models import VPNService, User

# --- Put your real numerical Telegram ID here ---
MY_TELEGRAM_ID = 271957957  

async def main():
    async with async_session_maker() as session:
        # Find user ID
        stmt = select(User).where(User.telegram_id == MY_TELEGRAM_ID)
        res = await session.execute(stmt)
        user = res.scalars().first()
        if not user:
            print("❌ کاربر در دیتابیس یافت نشد!")
            return
        
        # Delete test subscription records to reset your status [1]
        delete_stmt = delete(VPNService).where(VPNService.user_id == user.id)
        await session.execute(delete_stmt)
        await session.commit()
        print(f"✅ با موفقیت وضعیت تست برای کاربر {MY_TELEGRAM_ID} ریست شد!")

if __name__ == "__main__":
    asyncio.run(main())