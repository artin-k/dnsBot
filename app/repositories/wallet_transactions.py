from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import WalletTransaction, WalletTransactionStatus, WalletTransactionType


class WalletTransactionsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, transaction_id: int) -> WalletTransaction | None:
        return await self.session.get(WalletTransaction, transaction_id)

    async def get_with_details(self, transaction_id: int) -> WalletTransaction | None:
        return await self.session.scalar(
            select(WalletTransaction)
            .options(joinedload(WalletTransaction.user), joinedload(WalletTransaction.payment))
            .where(WalletTransaction.id == transaction_id)
        )

    async def get_by_payment_id(self, payment_id: int) -> WalletTransaction | None:
        return await self.session.scalar(
            select(WalletTransaction)
            .options(joinedload(WalletTransaction.user), joinedload(WalletTransaction.payment))
            .where(WalletTransaction.related_payment_id == payment_id)
        )

    async def list_recent_by_user(self, user_id: int, limit: int = 10) -> list[WalletTransaction]:
        result = await self.session.scalars(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == user_id)
            .order_by(WalletTransaction.created_at.desc())
            .limit(limit)
        )
        return list(result.all())

    async def list_pending_topups(self) -> list[WalletTransaction]:
        result = await self.session.scalars(
            select(WalletTransaction)
            .options(joinedload(WalletTransaction.user), joinedload(WalletTransaction.payment))
            .where(
                WalletTransaction.type == WalletTransactionType.TOPUP.value,
                WalletTransaction.status == WalletTransactionStatus.PENDING.value,
            )
            .order_by(WalletTransaction.created_at.asc())
        )
        return list(result.unique().all())

    async def create(
        self,
        *,
        user_id: int,
        amount: int,
        type: str,
        status: str,
        description: str | None = None,
        related_order_id: int | None = None,
        related_payment_id: int | None = None,
        approved_at: datetime | None = None,
    ) -> WalletTransaction:
        transaction = WalletTransaction(
            user_id=user_id,
            amount=amount,
            type=type,
            status=status,
            description=description,
            related_order_id=related_order_id,
            related_payment_id=related_payment_id,
            approved_at=approved_at,
        )
        self.session.add(transaction)
        await self.session.flush()
        return transaction
