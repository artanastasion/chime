"""add Translate domain tables

Revision ID: 4d8c7f1a2b3c
Revises: 325a27214760
Create Date: 2026-11-14

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d8c7f1a2b3c"
down_revision: Union[str, Sequence[str], None] = "325a27214760"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "track_features",
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column("feature_version", sa.String(length=50), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("energy", sa.Float(), nullable=True),
        sa.Column("valence", sa.Float(), nullable=True),
        sa.Column("tempo", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="ready", nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["track_id"], ["tracks.track_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("track_id", name=op.f("pk_track_features")),
    )

    op.create_table(
        "audio_analysis_jobs",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("feature_version", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "succeeded",
                "failed",
                name="audio_analysis_job_status",
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["media_assets.asset_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_audio_analysis_jobs")),
        sa.UniqueConstraint(
            "asset_id", "feature_version", name="uq_audio_analysis_jobs_asset_version"
        ),
    )
    op.create_index(
        op.f("ix_audio_analysis_jobs_asset_id"),
        "audio_analysis_jobs",
        ["asset_id"],
        unique=False,
    )

    op.create_table(
        "user_taste_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("genre_code", sa.String(length=50), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("weight >= 0 AND weight <= 1", name=op.f("ck_user_taste_preferences_weight_range")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(
            "user_id", "genre_code", name=op.f("pk_user_taste_preferences")
        ),
    )

    op.create_table(
        "translation_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=False),
        sa.Column("source_track_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_track_id"], ["tracks.track_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_translation_links")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_translation_links_token_hash")),
    )
    op.create_index(
        op.f("ix_translation_links_sender_id"),
        "translation_links",
        ["sender_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_translation_links_source_track_id"),
        "translation_links",
        ["source_track_id"],
        unique=False,
    )

    op.create_table(
        "translation_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("target_track_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("feature_version", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["link_id"], ["translation_links.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["target_track_id"], ["tracks.track_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_translation_results")),
    )
    op.create_index(
        op.f("ix_translation_results_link_id"),
        "translation_results",
        ["link_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_translation_results_recipient_id"),
        "translation_results",
        ["recipient_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_translation_results_target_track_id"),
        "translation_results",
        ["target_track_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_translation_results_target_track_id"), table_name="translation_results")
    op.drop_index(op.f("ix_translation_results_recipient_id"), table_name="translation_results")
    op.drop_index(op.f("ix_translation_results_link_id"), table_name="translation_results")
    op.drop_table("translation_results")
    op.drop_index(op.f("ix_translation_links_source_track_id"), table_name="translation_links")
    op.drop_index(op.f("ix_translation_links_sender_id"), table_name="translation_links")
    op.drop_table("translation_links")
    op.drop_table("user_taste_preferences")
    op.drop_index(op.f("ix_audio_analysis_jobs_asset_id"), table_name="audio_analysis_jobs")
    op.drop_table("audio_analysis_jobs")
    op.drop_table("track_features")
    sa.Enum(name="audio_analysis_job_status").drop(op.get_bind(), checkfirst=True)