"""001 초기 마이그레이션: 전체 테이블 생성

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# 리비전 식별자
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def _pg_enum(name: str) -> postgresql.ENUM:
    """이미 생성된 PostgreSQL ENUM 타입을 참조 (create_type=False)"""
    return postgresql.ENUM(name=name, create_type=False)


def upgrade() -> None:
    """전체 테이블 생성"""
    conn = op.get_bind()

    # === ENUM 타입 생성 (IF NOT EXISTS 방식) ===
    enum_defs = [
        ("user_role", ["admin", "user"]),
        ("dataset_source", ["upload", "builtin"]),
        ("step_type", ["analysis", "modeling", "optimization", "user_message", "assistant_message"]),
        ("step_status", ["pending", "running", "completed", "failed", "cancelled"]),
        ("artifact_type", ["dataframe", "plot", "model", "report", "shap", "feature_importance", "leaderboard"]),
        ("job_type", ["analysis", "baseline_modeling", "optimization", "shap", "plot_followup", "dataframe_followup"]),
        ("job_status", ["pending", "running", "completed", "failed", "cancelled"]),
        ("model_run_status", ["running", "completed", "failed"]),
        ("optimization_status", ["running", "completed", "failed", "cancelled"]),
    ]
    for name, values in enum_defs:
        values_sql = ", ".join(f"'{v}'" for v in values)
        conn.execute(sa.text(f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({values_sql}); EXCEPTION WHEN duplicate_object THEN null; END $$"))

    # === 테이블 생성 ===

    # users 테이블
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), unique=True, nullable=False),
        sa.Column("email", sa.String(256), unique=True, nullable=True),
        sa.Column("hashed_password", sa.String(256), nullable=False),
        sa.Column("role", _pg_enum("user_role"), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"])

    # auth_refresh_tokens 테이블
    op.create_table(
        "auth_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(256), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_revoked", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_auth_refresh_tokens_user_id", "auth_refresh_tokens", ["user_id"])
    op.create_index("ix_auth_refresh_tokens_token_hash", "auth_refresh_tokens", ["token_hash"])

    # datasets 테이블 (sessions보다 먼저 - 순환 참조 처리를 위해)
    op.create_table(
        "datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),  # FK는 나중에
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("source", _pg_enum("dataset_source"), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=True),
        sa.Column("file_path", sa.Text, nullable=True),
        sa.Column("builtin_key", sa.String(128), nullable=True),
        sa.Column("row_count", sa.BigInteger, nullable=True),
        sa.Column("col_count", sa.Integer, nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("schema_profile", postgresql.JSONB, nullable=True),
        sa.Column("missing_profile", postgresql.JSONB, nullable=True),
        sa.Column("target_candidates", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_datasets_session_id", "datasets", ["session_id"])

    # sessions 테이블
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("ttl_days", sa.Integer, nullable=False, server_default="7"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    # datasets에 session FK 추가
    op.create_foreign_key(
        "fk_datasets_session_id",
        "datasets", "sessions",
        ["session_id"], ["id"],
        ondelete="CASCADE",
    )

    # branches 테이블
    op.create_table(
        "branches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("config", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_branches_session_id", "branches", ["session_id"])

    # steps 테이블
    op.create_table(
        "steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_type", _pg_enum("step_type"), nullable=False),
        sa.Column("status", _pg_enum("step_status"), nullable=False, server_default="pending"),
        sa.Column("sequence_no", sa.Integer, nullable=False, server_default="0"),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("input_data", postgresql.JSONB, nullable=True),
        sa.Column("output_data", postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_steps_branch_id", "steps", ["branch_id"])

    # artifacts 테이블
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("steps.id", ondelete="CASCADE"), nullable=True),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True),
        sa.Column("artifact_type", _pg_enum("artifact_type"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("file_path", sa.Text, nullable=True),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("preview_json", postgresql.JSONB, nullable=True),
        sa.Column("meta", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_artifacts_step_id", "artifacts", ["step_id"])
    op.create_index("ix_artifacts_dataset_id", "artifacts", ["dataset_id"])

    # artifact_lineages 테이블
    op.create_table(
        "artifact_lineages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation_type", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_artifact_lineages_source", "artifact_lineages", ["source_artifact_id"])
    op.create_index("ix_artifact_lineages_target", "artifact_lineages", ["target_artifact_id"])

    # job_runs 테이블
    op.create_table(
        "job_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("steps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_type", _pg_enum("job_type"), nullable=False),
        sa.Column("status", _pg_enum("job_status"), nullable=False, server_default="pending"),
        sa.Column("rq_job_id", sa.String(256), nullable=True),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("progress_message", sa.Text, nullable=True),
        sa.Column("params", postgresql.JSONB, nullable=True),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("requested_cancel", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_job_runs_session_id", "job_runs", ["session_id"])
    op.create_index("ix_job_runs_user_id", "job_runs", ["user_id"])
    op.create_index("ix_job_runs_status", "job_runs", ["status"])
    op.create_index("ix_job_runs_rq_job_id", "job_runs", ["rq_job_id"])

    # model_runs 테이블
    op.create_table(
        "model_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("model_type", sa.String(64), nullable=False),
        sa.Column("status", _pg_enum("model_run_status"), nullable=False, server_default="running"),
        sa.Column("cv_rmse", sa.Float, nullable=True),
        sa.Column("cv_mae", sa.Float, nullable=True),
        sa.Column("cv_r2", sa.Float, nullable=True),
        sa.Column("test_rmse", sa.Float, nullable=True),
        sa.Column("test_mae", sa.Float, nullable=True),
        sa.Column("test_r2", sa.Float, nullable=True),
        sa.Column("n_train", sa.Integer, nullable=True),
        sa.Column("n_test", sa.Integer, nullable=True),
        sa.Column("n_features", sa.Integer, nullable=True),
        sa.Column("target_column", sa.String(256), nullable=True),
        sa.Column("hyperparams", postgresql.JSONB, nullable=True),
        sa.Column("feature_importances", postgresql.JSONB, nullable=True),
        sa.Column("is_champion", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("model_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_model_runs_branch_id", "model_runs", ["branch_id"])

    # optimization_runs 테이블
    op.create_table(
        "optimization_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("base_model_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", _pg_enum("optimization_status"), nullable=False, server_default="running"),
        sa.Column("n_trials", sa.Integer, nullable=False, server_default="50"),
        sa.Column("completed_trials", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metric", sa.String(64), nullable=False, server_default="rmse"),
        sa.Column("best_score", sa.Float, nullable=True),
        sa.Column("best_params", postgresql.JSONB, nullable=True),
        sa.Column("trials_history", postgresql.JSONB, nullable=True),
        sa.Column("study_name", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_optimization_runs_branch_id", "optimization_runs", ["branch_id"])

    # audit_logs 테이블
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(256), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("details", postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_session_id", "audit_logs", ["session_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    """전체 테이블 삭제"""
    op.drop_table("audit_logs")
    op.drop_table("optimization_runs")
    op.drop_table("model_runs")
    op.drop_table("job_runs")
    op.drop_table("artifact_lineages")
    op.drop_table("artifacts")
    op.drop_table("steps")
    op.drop_table("branches")
    op.drop_constraint("fk_datasets_session_id", "datasets", type_="foreignkey")
    op.drop_table("sessions")
    op.drop_table("datasets")
    op.drop_table("auth_refresh_tokens")
    op.drop_table("users")

    for enum_name in [
        "user_role", "dataset_source", "step_type", "step_status",
        "artifact_type", "job_type", "job_status", "model_run_status", "optimization_status"
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
