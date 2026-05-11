from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, ReferralReward, User, WalletTransactionStatus, WalletTransactionType
from app.repositories.wallet_transactions import WalletTransactionsRepository


class ReferralService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def grant_first_purchase_reward(self, *, user: User, order: Order, amount: int) -> ReferralReward | None:
        if amount <= 0 or user.referred_by_id is None:
            return None
        if user.referred_by_id == user.id:
            return None

        existing = await self.session.scalar(
            select(ReferralReward).where(ReferralReward.referred_user_id == user.id).with_for_update()
        )
        if existing is not None:
            return None

        referrer = await self.session.get(User, user.referred_by_id, with_for_update=True)
        if referrer is None:
            return None

        referrer.wallet_balance += amount
        reward = ReferralReward(
            referrer_id=referrer.id,
            referred_user_id=user.id,
            order_id=order.id,
            amount=amount,
        )
        self.session.add(reward)
        await WalletTransactionsRepository(self.session).create(
            user_id=referrer.id,
            amount=amount,
            type=WalletTransactionType.REFERRAL_REWARD.value,
            status=WalletTransactionStatus.APPROVED.value,
            description=f"پاداش زیرمجموعه‌گیری سفارش {order.tracking_code}",
            related_order_id=order.id,
        )
        await self.session.flush()
        return reward
