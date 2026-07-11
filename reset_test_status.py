import asyncio
from sqlalchemy import delete
from app.database import async_session_maker
from app.models import VPNService

async def main():
    async with async_session_maker() as session:
        # Delete all test subscription records for ALL users
        # It is critical to filter by is_test_account == True so you don't wipe paid plans!
        delete_stmt = delete(VPNService).where(VPNService.is_test_account == True)
        
        # Execute the deletion
        result = await session.execute(delete_stmt)
        await session.commit()
        
        # result.rowcount will tell you exactly how many test accounts were deleted
        deleted_count = result.rowcount
        
        print(f"✅ با موفقیت وضعیت تست برای تمامی کاربران ریست شد!")
        print(f"🗑 تعداد اکانت‌های تست پاک شده: {deleted_count}")

if __name__ == "__main__":
    asyncio.run(main())