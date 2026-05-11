from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import TestAccount, TestAccountClaim


class TestAccountsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, test_account_id: int) -> TestAccount | None:
        return await self.session.get(TestAccount, test_account_id)

    async def list_all(self) -> list[TestAccount]:
        result = await self.session.scalars(select(TestAccount).order_by(TestAccount.created_at.desc()))
        return list(result.all())

    async def get_user_claim(self, user_id: int) -> TestAccountClaim | None:
        return await self.session.scalar(
            select(TestAccountClaim)
            .options(joinedload(TestAccountClaim.test_account))
            .where(TestAccountClaim.user_id == user_id)
        )

    async def get_available(self) -> TestAccount | None:
        return await self.session.scalar(
            select(TestAccount)
            .where(
                TestAccount.is_active.is_(True),
                (TestAccount.max_claims == 0) | (TestAccount.claim_count < TestAccount.max_claims),
            )
            .order_by(TestAccount.created_at.asc())
        )

    async def create_claim(self, *, user_id: int, test_account: TestAccount) -> TestAccountClaim:
        claim = TestAccountClaim(user_id=user_id, test_account_id=test_account.id)
        test_account.claim_count += 1
        self.session.add(claim)
        await self.session.flush()
        return claim

    async def create(
        self,
        *,
        title: str,
        description: str | None,
        config_link: str,
        subscription_link: str | None,
        duration_hours: int,
        max_claims: int,
        is_active: bool = True,
    ) -> TestAccount:
        account = TestAccount(
            title=title,
            description=description,
            config_link=config_link,
            subscription_link=subscription_link,
            duration_hours=duration_hours,
            max_claims=max_claims,
            is_active=is_active,
        )
        self.session.add(account)
        await self.session.flush()
        return account

    async def has_claims(self, test_account_id: int) -> bool:
        return bool(
            await self.session.scalar(
                select(TestAccountClaim.id).where(TestAccountClaim.test_account_id == test_account_id).limit(1)
            )
        )
