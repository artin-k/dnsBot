# check_order_time.py
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from app.database import async_session_maker
from app.models import Order

async def main():
    async with async_session_maker() as session:
        # Get the latest order
        stmt = select(Order).order_by(Order.created_at.desc()).limit(1)
        res = await session.execute(stmt)
        order = res.scalars().first()
        
        now_utc = datetime.now(timezone.utc)
        now_naive = datetime.now()
        
        print(f"--- TIME DIAGNOSTIC ---")
        print(f"Python NOW (UTC):        {now_utc}")
        print(f"Python NOW (Naive Local): {now_naive}")
        
        if order:
            print(f"\nLast Order ID:           {order.id}")
            print(f"Order Status:            {order.status}")
            print(f"Order Created At (DB):   {order.created_at}")
            print(f"Order Expires At (DB):   {order.expires_at}")
            
            expires_utc = order.expires_at
            if expires_utc.tzinfo is None:
                expires_utc = expires_utc.replace(tzinfo=timezone.utc)
                
            is_expired = now_utc > expires_utc
            print(f"\nIs now_utc > expires_utc?  {is_expired} (If True, it is treated as expired) [1]")
        else:
            print("\nNo orders found in database.")

if __name__ == "__main__":
    asyncio.run(main())