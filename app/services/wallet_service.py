from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PaymentStatus, WalletTransactionStatus, WalletTransactionType
from app.repositories.payments import PaymentsRepository
from app.repositories.wallet_transactions import WalletTransactionsRepository


class WalletTopupError(Exception):
    pass


class WalletTopupAlreadyProcessedError(WalletTopupError):
    pass


@dataclass(frozen=True)
class WalletTopupResult:
    user_telegram_id: int
    amount: int
    wallet_balance: int


class WalletService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_topup_request(self, *, user_id: int, amount: int):
        payment = await PaymentsRepository(self.session).create(
            order_id=None,
            user_id=user_id,
            amount=amount,
            method="manual_wallet_topup",
            status=PaymentStatus.PENDING.value,
        )
        transaction = await WalletTransactionsRepository(self.session).create(
            user_id=user_id,
            amount=amount,
            type=WalletTransactionType.TOPUP.value,
            status=WalletTransactionStatus.PENDING.value,
            description="شارژ کیف پول",
            related_payment_id=payment.id,
        )
        await self.session.commit()
        return payment, transaction

    async def approve_topup(self, transaction_id: int) -> WalletTopupResult:
        transaction = await WalletTransactionsRepository(self.session).get_with_details(transaction_id)
        if transaction is None or transaction.payment is None:
            raise WalletTopupError("Wallet top-up not found")
        if transaction.status != WalletTransactionStatus.PENDING.value:
            raise WalletTopupAlreadyProcessedError("Wallet top-up already processed")

        now = datetime.now(timezone.utc)
        transaction.status = WalletTransactionStatus.APPROVED.value
        transaction.approved_at = now
        transaction.payment.status = PaymentStatus.APPROVED.value
        transaction.payment.verified_at = now
        transaction.user.wallet_balance += transaction.amount
        await self.session.commit()
        return WalletTopupResult(
            user_telegram_id=transaction.user.telegram_id,
            amount=transaction.amount,
            wallet_balance=transaction.user.wallet_balance,
        )

    async def reject_topup(self, transaction_id: int) -> WalletTopupResult:
        transaction = await WalletTransactionsRepository(self.session).get_with_details(transaction_id)
        if transaction is None or transaction.payment is None:
            raise WalletTopupError("Wallet top-up not found")
        if transaction.status != WalletTransactionStatus.PENDING.value:
            raise WalletTopupAlreadyProcessedError("Wallet top-up already processed")

        now = datetime.now(timezone.utc)
        transaction.status = WalletTransactionStatus.REJECTED.value
        transaction.approved_at = now
        transaction.payment.status = PaymentStatus.REJECTED.value
        transaction.payment.verified_at = now
        await self.session.commit()
        return WalletTopupResult(
            user_telegram_id=transaction.user.telegram_id,
            amount=transaction.amount,
            wallet_balance=transaction.user.wallet_balance,
        )
