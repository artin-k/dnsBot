"""add database backed settings

Revision ID: 20260514_0005
Revises: 20260512_0004
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260514_0005"
down_revision = "20260512_0004"
branch_labels = None
depends_on = None


settings_table = sa.table(
    "settings",
    sa.column("key", sa.String(length=128)),
    sa.column("value", sa.Text()),
    sa.column("value_type", sa.String(length=32)),
    sa.column("description", sa.Text()),
)


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.Text(), server_default="", nullable=False),
        sa.Column("value_type", sa.String(length=32), server_default="str", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.bulk_insert(
        settings_table,
        [
            {
                "key": "SUPPORT_USERNAME",
                "value": "",
                "value_type": "str",
                "description": "نام کاربری پشتیبانی تلگرام بدون @",
            },
            {
                "key": "PAYMENT_CARD_NUMBER",
                "value": "",
                "value_type": "str",
                "description": "شماره کارت برای پرداخت دستی",
            },
            {
                "key": "PAYMENT_CARD_HOLDER",
                "value": "",
                "value_type": "str",
                "description": "نام صاحب کارت پرداخت",
            },
            {
                "key": "PAYMENT_DESCRIPTION",
                "value": "پرداخت سفارش اشتراک VPN",
                "value_type": "str",
                "description": "توضیح نمایشی پرداخت دستی",
            },
            {
                "key": "ORDER_EXPIRE_MINUTES",
                "value": "15",
                "value_type": "int",
                "description": "مدت اعتبار سفارش پرداخت‌نشده به دقیقه",
            },
            {
                "key": "REFERRAL_REWARD_AMOUNT",
                "value": "0",
                "value_type": "int",
                "description": "پاداش کیف پول برای اولین خرید زیرمجموعه",
            },
            {
                "key": "WALLET_MIN_TOPUP_AMOUNT",
                "value": "50000",
                "value_type": "int",
                "description": "کمترین مبلغ مجاز برای شارژ کیف پول",
            },
            {
                "key": "WALLET_MAX_TOPUP_AMOUNT",
                "value": "0",
                "value_type": "int",
                "description": "بیشترین مبلغ مجاز شارژ کیف پول؛ 0 یعنی بدون محدودیت",
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("settings")
