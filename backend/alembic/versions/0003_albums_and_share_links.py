"""Albums, album members/items and public share links.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

from app.models import Album, AlbumItem, AlbumMember, ShareLink

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLES = [Album.__table__, AlbumItem.__table__, AlbumMember.__table__, ShareLink.__table__]


def upgrade() -> None:
    # checkfirst: on fresh databases 0001 already created these from the current models.
    for t in TABLES:
        t.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for t in reversed(TABLES):
        t.drop(op.get_bind(), checkfirst=True)
