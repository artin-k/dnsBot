from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MandatoryChannel


class MandatoryChannelsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_active(self) -> list[MandatoryChannel]:
        """Get all active mandatory channels."""
        result = await self.session.execute(
            select(MandatoryChannel).where(MandatoryChannel.is_active == True).order_by(MandatoryChannel.created_at)
        )
        return result.scalars().all()

    async def get_by_channel_id(self, channel_id: int) -> MandatoryChannel | None:
        """Get a mandatory channel by its channel_id."""
        result = await self.session.execute(select(MandatoryChannel).where(MandatoryChannel.channel_id == channel_id))
        return result.scalars().first()

    async def create(self, channel_id: int, channel_name: str, invite_link: str) -> MandatoryChannel:
        """Create a new mandatory channel."""
        channel = MandatoryChannel(
            channel_id=channel_id,
            channel_name=channel_name,
            invite_link=invite_link,
            is_active=True,
        )
        self.session.add(channel)
        await self.session.flush()
        return channel

    async def delete_by_id(self, channel_db_id: int) -> bool:
        """Delete a mandatory channel by its database ID. Returns True if deleted."""
        result = await self.session.execute(delete(MandatoryChannel).where(MandatoryChannel.id == channel_db_id))
        return result.rowcount > 0

    async def delete_by_channel_id(self, channel_id: int) -> bool:
        """Delete a mandatory channel by its channel_id. Returns True if deleted."""
        result = await self.session.execute(delete(MandatoryChannel).where(MandatoryChannel.channel_id == channel_id))
        return result.rowcount > 0

    async def exists(self, channel_id: int) -> bool:
        """Check if a channel already exists."""
        result = await self.session.execute(
            select(MandatoryChannel).where(MandatoryChannel.channel_id == channel_id).limit(1)
        )
        return result.scalars().first() is not None
