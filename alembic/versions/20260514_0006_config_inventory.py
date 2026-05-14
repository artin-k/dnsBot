"""add config inventory

Revision ID: 20260514_0006
Revises: 20260514_0005
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260514_0006"
down_revision = "20260514_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_inventory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("config_link", sa.Text(), nullable=True),
        sa.Column("subscription_link", sa.Text(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="available", nullable=False),
        sa.Column("reserved_by_order_id", sa.Integer(), nullable=True),
        sa.Column("sold_to_user_id", sa.Integer(), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "(config_link IS NOT NULL AND config_link <> '') OR "
            "(subscription_link IS NOT NULL AND subscription_link <> '')",
            name="ck_config_inventory_has_link",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reserved_by_order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sold_to_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_config_inventory_plan_id"), "config_inventory", ["plan_id"])
    op.create_index(op.f("ix_config_inventory_reserved_by_order_id"), "config_inventory", ["reserved_by_order_id"])
    op.create_index(op.f("ix_config_inventory_sold_to_user_id"), "config_inventory", ["sold_to_user_id"])
    op.create_index(op.f("ix_config_inventory_status"), "config_inventory", ["status"])
    op.create_index(op.f("ix_config_inventory_username"), "config_inventory", ["username"])

    op.add_column("orders", sa.Column("config_inventory_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_orders_config_inventory_id_config_inventory"),
        "orders",
        "config_inventory",
        ["config_inventory_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_orders_config_inventory_id"), "orders", ["config_inventory_id"])

    op.add_column("vpn_services", sa.Column("config_inventory_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_vpn_services_config_inventory_id_config_inventory"),
        "vpn_services",
        "config_inventory",
        ["config_inventory_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_vpn_services_config_inventory_id"), "vpn_services", ["config_inventory_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_vpn_services_config_inventory_id"), table_name="vpn_services")
    op.drop_constraint(op.f("fk_vpn_services_config_inventory_id_config_inventory"), "vpn_services", type_="foreignkey")
    op.drop_column("vpn_services", "config_inventory_id")

    op.drop_index(op.f("ix_orders_config_inventory_id"), table_name="orders")
    op.drop_constraint(op.f("fk_orders_config_inventory_id_config_inventory"), "orders", type_="foreignkey")
    op.drop_column("orders", "config_inventory_id")

    op.drop_index(op.f("ix_config_inventory_username"), table_name="config_inventory")
    op.drop_index(op.f("ix_config_inventory_status"), table_name="config_inventory")
    op.drop_index(op.f("ix_config_inventory_sold_to_user_id"), table_name="config_inventory")
    op.drop_index(op.f("ix_config_inventory_reserved_by_order_id"), table_name="config_inventory")
    op.drop_index(op.f("ix_config_inventory_plan_id"), table_name="config_inventory")
    op.drop_table("config_inventory")
