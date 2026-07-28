import asyncio
from sqlalchemy import delete, select
from app.database import async_session_maker
from app.models import VPNService, IPAuthToken

async def main():
    async with async_session_maker() as session:
        # 1. Fetch test service IDs first
        stmt = select(VPNService.id).where(VPNService.is_test_account == True)
        res = await session.execute(stmt)
        test_service_ids = res.scalars().all()

        if test_service_ids:
            # 2. Delete linked tokens first to satisfy foreign key constraints
            await session.execute(
                delete(IPAuthToken).where(IPAuthToken.service_id.in_(test_service_ids))
            )
            
            # 3. Delete test subscription records
            delete_stmt = delete(VPNService).where(VPNService.id.in_(test_service_ids))
            result = await session.execute(delete_stmt)
            await session.commit()
            deleted_count = result.rowcount
        else:
            deleted_count = 0

        print(f"✅ با موفقیت وضعیت تست برای تمامی کاربران ریست شد!")
        print(f"🗑 تعداد اکانت‌های تست پاک شده: {deleted_count}")

if __name__ == "__main__":
    asyncio.run(main())