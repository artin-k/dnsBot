from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> AppSetting | None:
        return await self.session.get(AppSetting, key)

    async def list_all(self) -> list[AppSetting]:
        result = await self.session.scalars(select(AppSetting).order_by(AppSetting.key.asc()))
        return list(result.all())

    async def list_by_keys(self, keys: list[str]) -> list[AppSetting]:
        if not keys:
            return []
        result = await self.session.scalars(select(AppSetting).where(AppSetting.key.in_(keys)))
        return list(result.all())

    async def upsert(
        self,
        *,
        key: str,
        value: str,
        value_type: str = "str",
        description: str | None = None,
    ) -> AppSetting:
        setting = await self.get(key)
        if setting is None:
            setting = AppSetting(
                key=key,
                value=value,
                value_type=value_type,
                description=description,
            )
            self.session.add(setting)
        else:
            setting.value = value
            setting.value_type = value_type
            setting.description = description
        await self.session.flush()
        return setting
