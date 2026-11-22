"""Files that failed processing have no preview to detect faces on: mark them skipped, not pending.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("update files set face_state = 'skip' where process_state = 'error' and face_state = 'pending'")


def downgrade() -> None:
    op.execute("update files set face_state = 'pending' where process_state = 'error' and face_state = 'skip'")
