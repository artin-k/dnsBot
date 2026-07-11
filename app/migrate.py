# Create this as app/migrate.py
import asyncio
import structlog
from sqlalchemy import text
from app.database import engine

logger = structlog.get_logger(__name__)

# app/migrate.py

# app/migrate.py

async def run_migrations():
    print("Connecting to database and running table updates...")
    
    async with engine.begin() as conn:
        # Create ip_auth_tokens table natively [cite: 1]
        try:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS ip_auth_tokens (
                    id SERIAL PRIMARY KEY,
                    token VARCHAR(64) UNIQUE NOT NULL,
                    service_id INTEGER NOT NULL REFERENCES vpn_services(id) ON DELETE CASCADE,
                    is_used BOOLEAN NOT NULL DEFAULT FALSE,
                    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ip_auth_tokens_token ON ip_auth_tokens(token);"))
            print("✅ Successfully generated 'ip_auth_tokens' table on PostgreSQL.")
        except Exception as e:
            print(f"❌ Error creating 'ip_auth_tokens' table: {e}")

if __name__ == "__main__":
    asyncio.run(run_migrations())