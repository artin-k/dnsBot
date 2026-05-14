from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import ConfigInventory, ConfigInventoryStatus, Plan


class ConfigInventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, item_id: int) -> ConfigInventory | None:
        return await self.session.get(ConfigInventory, item_id)

    async def get_with_details(self, item_id: int) -> ConfigInventory | None:
        return await self.session.scalar(
            select(ConfigInventory)
            .options(
                joinedload(ConfigInventory.plan),
                joinedload(ConfigInventory.reserved_order),
                joinedload(ConfigInventory.sold_to_user),
            )
            .where(ConfigInventory.id == item_id)
        )

    async def get_reserved_for_order(self, order_id: int) -> ConfigInventory | None:
        return await self.session.scalar(
            select(ConfigInventory)
            .where(
                ConfigInventory.reserved_by_order_id == order_id,
                ConfigInventory.status == ConfigInventoryStatus.RESERVED.value,
            )
        )

    async def get_available_for_update(self, plan_id: int) -> ConfigInventory | None:
        return await self.session.scalar(
            select(ConfigInventory)
            .where(
                ConfigInventory.plan_id == plan_id,
                ConfigInventory.status == ConfigInventoryStatus.AVAILABLE.value,
            )
            .order_by(ConfigInventory.created_at.asc(), ConfigInventory.id.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )

    async def count_by_status(self, plan_id: int, status: str) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(ConfigInventory)
                .where(ConfigInventory.plan_id == plan_id, ConfigInventory.status == status)
            )
            or 0
        )

    async def counts_by_plan(self) -> dict[int, dict[str, int]]:
        rows = await self.session.execute(
            select(ConfigInventory.plan_id, ConfigInventory.status, func.count(ConfigInventory.id))
            .group_by(ConfigInventory.plan_id, ConfigInventory.status)
        )
        counts: dict[int, dict[str, int]] = {}
        for plan_id, status, count in rows:
            counts.setdefault(int(plan_id), {})[str(status)] = int(count)
        return counts

    async def available_counts_for_plans(self, plan_ids: Iterable[int]) -> dict[int, int]:
        ids = list(plan_ids)
        if not ids:
            return {}
        rows = await self.session.execute(
            select(ConfigInventory.plan_id, func.count(ConfigInventory.id))
            .where(
                ConfigInventory.plan_id.in_(ids),
                ConfigInventory.status == ConfigInventoryStatus.AVAILABLE.value,
            )
            .group_by(ConfigInventory.plan_id)
        )
        return {int(plan_id): int(count) for plan_id, count in rows}

    async def list_items(
        self,
        *,
        plan_id: int | None = None,
        status: str | None = None,
        page: int = 0,
        page_size: int = 10,
    ) -> tuple[list[ConfigInventory], bool]:
        query = (
            select(ConfigInventory)
            .options(joinedload(ConfigInventory.plan), joinedload(ConfigInventory.sold_to_user))
            .order_by(ConfigInventory.created_at.desc(), ConfigInventory.id.desc())
        )
        if plan_id:
            query = query.where(ConfigInventory.plan_id == plan_id)
        if status and status != "all":
            query = query.where(ConfigInventory.status == status)
        result = await self.session.scalars(query.offset(max(page, 0) * page_size).limit(page_size + 1))
        items = list(result.unique().all())
        return items[:page_size], len(items) > page_size

    async def search(self, query_text: str, limit: int = 10) -> list[ConfigInventory]:
        normalized = query_text.strip()
        if not normalized:
            return []
        conditions = [
            ConfigInventory.config_link.ilike(f"%{normalized}%"),
            ConfigInventory.subscription_link.ilike(f"%{normalized}%"),
            ConfigInventory.title.ilike(f"%{normalized}%"),
            ConfigInventory.username.ilike(f"%{normalized}%"),
            ConfigInventory.note.ilike(f"%{normalized}%"),
        ]
        if normalized.isdigit():
            conditions.append(ConfigInventory.id == int(normalized))
        result = await self.session.scalars(
            select(ConfigInventory)
            .options(joinedload(ConfigInventory.plan), joinedload(ConfigInventory.sold_to_user))
            .where(or_(*conditions))
            .order_by(ConfigInventory.created_at.desc())
            .limit(limit)
        )
        return list(result.unique().all())

    async def create(
        self,
        *,
        plan_id: int,
        config_link: str | None,
        subscription_link: str | None = None,
        title: str | None = None,
        username: str | None = None,
        note: str | None = None,
        status: str = ConfigInventoryStatus.AVAILABLE.value,
    ) -> ConfigInventory:
        item = ConfigInventory(
            plan_id=plan_id,
            title=title,
            config_link=config_link,
            subscription_link=subscription_link,
            username=username,
            note=note,
            status=status,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def plan_ids_low_or_empty(self, threshold: int) -> list[Plan]:
        available_counts = (
            select(ConfigInventory.plan_id, func.count(ConfigInventory.id).label("available_count"))
            .where(ConfigInventory.status == ConfigInventoryStatus.AVAILABLE.value)
            .group_by(ConfigInventory.plan_id)
            .subquery()
        )
        result = await self.session.scalars(
            select(Plan)
            .outerjoin(available_counts, available_counts.c.plan_id == Plan.id)
            .where(
                Plan.is_active.is_(True),
                func.coalesce(available_counts.c.available_count, 0) <= threshold,
            )
            .order_by(Plan.sort_order.asc(), Plan.id.asc())
        )
        return list(result.all())
