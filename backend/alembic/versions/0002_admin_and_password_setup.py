"""Admin flag, first-login password setup, user deactivation.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NEW_COLUMNS = [
    sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("must_set_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
]


def upgrade() -> None:
    # 0001 creates tables from the current models, so on a fresh database these columns
    # already exist; only add what's missing (databases created before this revision).
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    for col in NEW_COLUMNS:
        if col.name not in existing:
            op.add_column("users", col)
    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    for col in NEW_COLUMNS:
        op.drop_column("users", col.name)
