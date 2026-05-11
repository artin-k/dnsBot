"""add wallet verification, test accounts, dice discounts

Revision ID: 20260511_0003
Revises: 20260511_0002
Create Date: 2026-05-11 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260511_0003"
down_revision = "20260511_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone_number", sa.String(length=32), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_phone_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("users", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("orders", sa.Column("discount_code", sa.String(length=32), nullable=True))
    op.add_column("orders", sa.Column("discount_percent", sa.Integer(), server_default="0", nullable=False))
    op.add_column("orders", sa.Column("discount_amount", sa.Integer(), server_default="0", nullable=False))

    op.alter_column("payments", "order_id", existing_type=sa.Integer(), nullable=True)

    op.create_table(
        "wallet_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("related_order_id", sa.Integer(), nullable=True),
        sa.Column("related_payment_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["related_order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["related_payment_id"], ["payments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_wallet_transactions_related_order_id"), "wallet_transactions", ["related_order_id"])
    op.create_index(op.f("ix_wallet_transactions_related_payment_id"), "wallet_transactions", ["related_payment_id"])
    op.create_index(op.f("ix_wallet_transactions_user_id"), "wallet_transactions", ["user_id"])

    op.create_table(
        "test_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("config_link", sa.Text(), nullable=False),
        sa.Column("subscription_link", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("max_claims", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claim_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_hours", sa.Integer(), server_default="24", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "test_account_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("test_account_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["test_account_id"], ["test_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_test_account_claims_user_id"),
    )
    op.create_index(op.f("ix_test_account_claims_test_account_id"), "test_account_claims", ["test_account_id"])
    op.create_index(op.f("ix_test_account_claims_user_id"), "test_account_claims", ["user_id"])

    op.create_table(
        "dice_rolls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("dice_value", sa.Integer(), nullable=False),
        sa.Column("won", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("discount_percent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("discount_code", sa.String(length=32), nullable=True),
        sa.Column("used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("discount_code"),
    )
    op.create_index(op.f("ix_dice_rolls_discount_code"), "dice_rolls", ["discount_code"])
    op.create_index(op.f("ix_dice_rolls_user_id"), "dice_rolls", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_dice_rolls_user_id"), table_name="dice_rolls")
    op.drop_index(op.f("ix_dice_rolls_discount_code"), table_name="dice_rolls")
    op.drop_table("dice_rolls")
    op.drop_index(op.f("ix_test_account_claims_user_id"), table_name="test_account_claims")
    op.drop_index(op.f("ix_test_account_claims_test_account_id"), table_name="test_account_claims")
    op.drop_table("test_account_claims")
    op.drop_table("test_accounts")
    op.drop_index(op.f("ix_wallet_transactions_user_id"), table_name="wallet_transactions")
    op.drop_index(op.f("ix_wallet_transactions_related_payment_id"), table_name="wallet_transactions")
    op.drop_index(op.f("ix_wallet_transactions_related_order_id"), table_name="wallet_transactions")
    op.drop_table("wallet_transactions")
    op.alter_column("payments", "order_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("orders", "discount_amount")
    op.drop_column("orders", "discount_percent")
    op.drop_column("orders", "discount_code")
    op.drop_column("users", "verified_at")
    op.drop_column("users", "is_phone_verified")
    op.drop_column("users", "phone_number")
