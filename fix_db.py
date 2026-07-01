import asyncio
import asyncpg
from app.config import get_settings

async def fix_database():
    # 1. Grab the exact settings the bot uses (from your .env file)
    settings = get_settings()
    
    # 2. Format the URL so asyncpg can read it 
    # (removing the '+asyncpg' part if it exists in your string)
    db_url = settings.database_url.replace("+asyncpg", "")
    
    print("Connecting to the database using your .env credentials...")
    
    # 3. Connect!
    try:
        conn = await asyncpg.connect(db_url)
        print("Injecting the 'token' column...")
        await conn.execute("ALTER TABLE payments ADD COLUMN token VARCHAR;")
        print("✅ Success! Column 'token' added to the payments table.")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()

asyncio.run(fix_database())