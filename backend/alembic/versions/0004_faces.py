"""Face recognition: real people/faces tables and per-file face state.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

from app.models import Face, Person

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    face_cols = {c["name"] for c in insp.get_columns("faces")} if insp.has_table("faces") else set()
    if "vector" not in face_cols:
        # Old v1 placeholders (never used, always empty): replace with the real schema.
        for table in ("faces", "people"):
            if insp.has_table(table):
                count = bind.execute(sa.text(f"select count(*) from {table}")).scalar()
                if count:
                    raise RuntimeError(f"refusing to replace non-empty placeholder table {table!r}")
                op.drop_table(table)
        Person.__table__.create(bind)
        Face.__table__.create(bind)
    file_cols = {c["name"] for c in insp.get_columns("files")}
    if "face_state" not in file_cols:
        op.add_column("files", sa.Column("face_state", sa.String(8), nullable=False, server_default="pending"))
        op.create_index("ix_files_face_state", "files", ["face_state"])


def downgrade() -> None:
    op.drop_index("ix_files_face_state", "files")
    op.drop_column("files", "face_state")
