"""Database schema."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # NULL until the user sets a password at first sign-in.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Top-level folder in the library this user uploads to (e.g. "David"). Determines ownership.
    home_folder: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    # Admins can additionally create and manage users; everything else is identical.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # When true, the next sign-in asks the user to choose a new password (no old password needed).
    must_set_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Deactivated users can't sign in (their history and file ownership are kept).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Item(Base):
    """A logical unit of media. Usually one file; a Live Photo is a still + its short video."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    files: Mapped[list["File"]] = relationship(back_populates="item", order_by="File.id")

    @property
    def primary(self) -> "File | None":
        for f in self.files:
            if f.role == "primary":
                return f
        return self.files[0] if self.files else None


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Path relative to the library root, forward slashes (identical on NAS and PC workers).
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(16), default="primary")  # primary | live_video

    size: Mapped[int] = mapped_column(BigInteger, index=True)
    mtime: Mapped[float] = mapped_column(Float)
    ext: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(8), index=True)  # image | video
    mime: Mapped[str | None] = mapped_column(String(64), nullable=True)

    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    phash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    dhash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    video_sig: Mapped[list | None] = mapped_column(JSON, nullable=True)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(32), nullable=True)

    taken_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    make: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lens: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gps_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_exif: Mapped[bool] = mapped_column(Boolean, default=False)
    exif_score: Mapped[int] = mapped_column(Integer, default=0)
    live_photo_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    exif: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # active | pending_delete | quarantined | purged | missing
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # pending | done | error
    process_state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    process_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumb_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    # Face detection: pending | done | error | skip (videos, unreadable previews)
    face_state: Mapped[str] = mapped_column(String(8), default="pending", server_default="pending", index=True)

    # While quarantined, `path` points into .quarantine/ and this holds where it came from.
    original_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    item: Mapped[Item | None] = relationship(back_populates="files")
    owner: Mapped[User | None] = relationship()

    @property
    def pixels(self) -> int:
        return (self.width or 0) * (self.height or 0)

    @property
    def has_gps(self) -> bool:
        return self.gps_lat is not None and self.gps_lon is not None


class DupGroup(Base):
    __tablename__ = "dup_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(16), index=True)  # exact | reencode | burst | video
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | resolved | dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    members: Mapped[list["DupGroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", order_by="DupGroupMember.id"
    )

    @property
    def category(self) -> str:
        return "burst" if self.type == "burst" else "strong"


class DupGroupMember(Base):
    __tablename__ = "dup_group_members"
    __table_args__ = (UniqueConstraint("group_id", "item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dup_groups.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    match_type: Mapped[str] = mapped_column(String(16), default="exact")
    suggested_keep: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    group: Mapped[DupGroup] = relationship(back_populates="members")
    item: Mapped[Item] = relationship()


# Decision states that count as "the current decision" for a (group, item).
ACTIVE_STATES = ("recorded", "marked", "approved", "quarantined", "purged")
# States in which an item is (or will be) gone.
DELETE_STATES = ("marked", "approved", "quarantined", "purged")


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("dup_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    action: Mapped[str] = mapped_column(String(8))  # keep | delete | skip
    # recorded (keep/skip) | marked -> approved -> quarantined -> purged
    # | rejected | cancelled | restored | superseded
    state: Mapped[str] = mapped_column(String(16), index=True)
    marked_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    marked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    item: Mapped[Item] = relationship()
    group: Mapped[DupGroup | None] = relationship()
    marker: Mapped[User] = relationship(foreign_keys=[marked_by])
    approver: Mapped[User | None] = relationship(foreign_keys=[approved_by])


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    target_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user: Mapped[User | None] = relationship()


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(16), index=True)  # scan | process | group | preview | purge
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued | running | done | error
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    worker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | int | float | bool | str | None] = mapped_column(JSON)


# --- v2 placeholders (face recognition) -------------------------------------------------


class Person(Base):
    """Someone recognised in photos. Unnamed rows are automatic clusters waiting for a name."""

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # Hidden = "don't show this person/cluster" (strangers, statues...). Faces stay assigned to it.
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    cover_face_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # An admin chose cover_face_id: keep it (until that face leaves this person or its photo goes).
    cover_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Face(Base):
    """A detected face in a photo (coordinates relative to the displayed, rotation-corrected image)."""

    __tablename__ = "faces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    bbox: Mapped[list] = mapped_column(JSON)  # [x1, y1, x2, y2] as fractions of width/height
    score: Mapped[float] = mapped_column(Float, default=0.0)  # detector confidence
    size_px: Mapped[int] = mapped_column(Integer, default=0)  # face size in the 2048px preview
    quality: Mapped[bool] = mapped_column(Boolean, default=True)  # big/clear enough to recognise reliably
    vector: Mapped[bytes] = mapped_column(LargeBinary)  # 512 x float32, L2-normalised (ArcFace)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # none | cluster (automatic unnamed group) | auto (recognised as a named person) | manual (set by a user)
    assignment: Mapped[str] = mapped_column(String(8), default="none", index=True)
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Uncertain match offered for review ("Is this Sue?").
    suggested_person_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # People a user said this face is NOT; never auto-assigned to them again.
    rejected: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- albums & sharing ---------------------------------------------------------------------


class Album(Base):
    """A hand-picked collection of items. Items are referenced, never copied."""

    __tablename__ = "albums"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    cover_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    owner: Mapped[User] = relationship()
    items: Mapped[list["AlbumItem"]] = relationship(
        back_populates="album", cascade="all, delete-orphan", order_by="AlbumItem.position"
    )
    members: Mapped[list["AlbumMember"]] = relationship(back_populates="album", cascade="all, delete-orphan")
    links: Mapped[list["ShareLink"]] = relationship(back_populates="album", cascade="all, delete-orphan")


class AlbumItem(Base):
    __tablename__ = "album_items"
    __table_args__ = (UniqueConstraint("album_id", "item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("albums.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    position: Mapped[float] = mapped_column(Float, default=0.0)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    album: Mapped[Album] = relationship(back_populates="items")
    item: Mapped[Item] = relationship()


class AlbumMember(Base):
    """Another app user the owner shared the album with. role: viewer | editor (can add/remove/reorder)."""

    __tablename__ = "album_members"
    __table_args__ = (UniqueConstraint("album_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("albums.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(8), default="viewer")

    album: Mapped[Album] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class ShareLink(Base):
    """Public read-only link to an album for people without an account."""

    __tablename__ = "share_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("albums.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    allow_download: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    album: Mapped[Album] = relationship(back_populates="links")
