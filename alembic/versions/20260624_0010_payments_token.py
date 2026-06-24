"""add payment token tracking

Revision ID: 20260624_0010
Revises: 20260608_0009
Create Date: 2026-06-24 00:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260624_0010"
down_revision = "20260608_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("token", sa.String(length=255), nullable=True))
    op.execute(sa.text("UPDATE payments SET token = 'legacy_payment_' || id WHERE token IS NULL"))
    op.alter_column(
        "payments",
        "token",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_unique_constraint(op.f("payments_token_key"), "payments", ["token"])


def downgrade() -> None:
    op.drop_constraint(op.f("payments_token_key"), "payments", type_="unique")
    op.drop_column("payments", "token")
