"""001 초기 마이그레이션 (SQLite)

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(256), nullable=True, unique=True),
        sa.Column("hashed_password", sa.String(256), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, default="user"),
        sa.Column("is_active", sa.Boolean, nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "auth_refresh_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(256), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_revoked", sa.Boolean, nullable=False, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_auth_refresh_tokens_user_id", "auth_refresh_tokens", ["user_id"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("active_dataset_id", sa.String(36), nullable=True),
        sa.Column("name", sa.String(256), nullable=True),
        sa.Column("ttl_days", sa.Integer, nullable=False, default=7),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("file_path", sa.Text, nullable=True),
        sa.Column("row_count", sa.Integer, nullable=True),
        sa.Column("col_count", sa.Integer, nullable=True),
        sa.Column("file_size_bytes", sa.Integer, nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("builtin_key", sa.String(128), nullable=True),
        sa.Column("schema_profile", sa.JSON, nullable=True),
        sa.Column("missing_profile", sa.JSON, nullable=True),
        sa.Column("target_candidates", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_datasets_session_id", "datasets", ["session_id"])

    op.create_table(
        "branches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_branch_id", sa.String(36), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, default=True),
        sa.Column("config", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_branches_session_id", "branches", ["session_id"])

    op.create_table(
        "steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("branch_id", sa.String(36), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, default="pending"),
        sa.Column("sequence_no", sa.Integer, nullable=False, default=0),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("input_data", sa.JSON, nullable=True),
        sa.Column("output_data", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_steps_branch_id", "steps", ["branch_id"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("step_id", sa.String(36), sa.ForeignKey("steps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dataset_id", sa.String(36), nullable=True),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("file_path", sa.Text, nullable=True),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("file_size_bytes", sa.Integer, nullable=True),
        sa.Column("preview_json", sa.JSON, nullable=True),
        sa.Column("meta", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_artifacts_step_id", "artifacts", ["step_id"])

    op.create_table(
        "artifact_lineages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "job_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(36), sa.ForeignKey("steps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, default="pending"),
        sa.Column("rq_job_id", sa.String(256), nullable=True),
        sa.Column("progress", sa.Integer, nullable=False, default=0),
        sa.Column("progress_message", sa.Text, nullable=True),
        sa.Column("params", sa.JSON, nullable=True),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_job_runs_session_id", "job_runs", ["session_id"])
    op.create_index("ix_job_runs_user_id", "job_runs", ["user_id"])
    op.create_index("ix_job_runs_status", "job_runs", ["status"])

    op.create_table(
        "model_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("branch_id", sa.String(36), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_run_id", sa.String(36), nullable=True),
        sa.Column("model_artifact_id", sa.String(36), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("model_type", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, default="pending"),
        sa.Column("is_champion", sa.Boolean, nullable=False, default=False),
        sa.Column("target_column", sa.String(128), nullable=True),
        sa.Column("train_rmse", sa.Float, nullable=True),
        sa.Column("train_mae", sa.Float, nullable=True),
        sa.Column("train_r2", sa.Float, nullable=True),
        sa.Column("cv_rmse", sa.Float, nullable=True),
        sa.Column("cv_mae", sa.Float, nullable=True),
        sa.Column("cv_r2", sa.Float, nullable=True),
        sa.Column("test_rmse", sa.Float, nullable=True),
        sa.Column("test_mae", sa.Float, nullable=True),
        sa.Column("test_r2", sa.Float, nullable=True),
        sa.Column("hyperparams", sa.JSON, nullable=True),
        sa.Column("feature_importances", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_model_runs_branch_id", "model_runs", ["branch_id"])

    op.create_table(
        "optimization_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("branch_id", sa.String(36), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_run_id", sa.String(36), nullable=True),
        sa.Column("base_model_run_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, default="pending"),
        sa.Column("n_trials", sa.Integer, nullable=False, default=50),
        sa.Column("completed_trials", sa.Integer, nullable=False, default=0),
        sa.Column("metric", sa.String(64), nullable=True),
        sa.Column("study_name", sa.String(256), nullable=True),
        sa.Column("best_params", sa.JSON, nullable=True),
        sa.Column("trials_history", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("session_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(36), nullable=True),
        sa.Column("details", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    for table in [
        "audit_logs", "optimization_runs", "model_runs", "job_runs",
        "artifact_lineages", "artifacts", "steps", "branches",
        "datasets", "sessions", "auth_refresh_tokens", "users",
    ]:
        op.drop_table(table)
