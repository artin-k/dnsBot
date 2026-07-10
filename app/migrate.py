# Create this as app/migrate.py
import asyncio
import structlog
from sqlalchemy import text
from app.database import engine

logger = structlog.get_logger(__name__)

# app/migrate.py

async def run_migrations():
    print("Connecting to database and running table updates...")
    
    async with engine.begin() as conn:
        # Ensure column exists
        try:
            await conn.execute(text("ALTER TABLE vpn_services ADD COLUMN IF NOT EXISTS authorized_ip VARCHAR(45);"))
            print("✅ Successfully updated 'vpn_services' table with 'authorized_ip' column.")
        except Exception as e:
            print(f"❌ Error updating 'vpn_services' table: {e}")

    await engine.dispose()
    print("Database update complete.")

if __name__ == "__main__":
    asyncio.run(run_migrations())