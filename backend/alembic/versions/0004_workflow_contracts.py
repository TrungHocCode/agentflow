"""Normalize version steps, create task identities, and scope run idempotency."""

from __future__ import annotations

import uuid
import hashlib
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0004_workflow_contracts"
down_revision: Union[str, Sequence[str], None] = "0003_research_outputs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind: sa.Connection, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def _step_id(version_id: str, task_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agentflow:{version_id}:step:{task_key}"))


def _task_execution_id(run_id: str, task_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agentflow:{run_id}:task:{task_key}"))


def _plan_revision(plan: object) -> str:
    fields = (
        "task_key", "id", "node", "agent_id", "capability", "tool_names", "tool_ids",
        "description", "dependencies", "timeout_seconds", "max_iterations",
        "expected_output_type", "input_mapping", "config",
    )
    canonical_tasks = []
    if isinstance(plan, list):
        for task in plan:
            if not isinstance(task, dict):
                continue
            canonical = {key: task.get(key) for key in fields}
            canonical["task_key"] = task.get("task_key") or str(task.get("id", ""))
            canonical["tool_names"] = task.get("tool_names") or []
            canonical["tool_ids"] = task.get("tool_ids") or []
            canonical["dependencies"] = task.get("dependencies") or []
            canonical["expected_output_type"] = task.get("expected_output_type") or "raw_data"
            canonical["input_mapping"] = task.get("input_mapping") or {}
            canonical["config"] = task.get("config") or {}
            canonical_tasks.append(canonical)
    serialized = json.dumps(canonical_tasks, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _legacy_to_steps(definition: object) -> list[dict[str, object]]:
    if not isinstance(definition, dict):
        return []
    steps = definition.get("steps")
    if isinstance(steps, list):
        return [step for step in steps if isinstance(step, dict)]
    tasks = definition.get("tasks")
    if not isinstance(tasks, list):
        return []
    keyed: list[tuple[str, dict[str, object]]] = []
    for index, task in enumerate(tasks, start=1):
        if isinstance(task, dict):
            keyed.append((str(task.get("task_key") or task.get("id") or index), task))
    id_to_key = {
        str(task.get("id", key)): key
        for key, task in keyed
    }
    converted: list[dict[str, object]] = []
    for position, (key, task) in enumerate(keyed):
        dependencies = [
            id_to_key.get(str(dependency), str(dependency))
            for dependency in (task.get("dependencies") or [])
        ]
        config = task.get("config") if isinstance(task.get("config"), dict) else {}
        converted.append(
            {
                "task_key": key,
                "name": task.get("node") or key,
                "description": task.get("description") or task.get("node") or key,
                "agent_id": task.get("agent_id") or task.get("node"),
                "dependencies": dependencies,
                "expected_output_type": task.get("expected_output_type") or "raw_data",
                "position": position,
                "config": config,
                "input_mapping": task.get("input_mapping") or {},
                "tool_ids": task.get("tool_ids") or task.get("tool_names") or [],
            }
        )
    return converted


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    run_columns = {column["name"] for column in inspector.get_columns("runs")}
    if "idempotency_fingerprint" not in run_columns:
        op.add_column("runs", sa.Column("idempotency_fingerprint", sa.String(64), nullable=True))
    if "plan_revision" not in run_columns:
        op.add_column("runs", sa.Column("plan_revision", sa.String(64), nullable=True))
    if "approved_plan_revision" not in run_columns:
        op.add_column("runs", sa.Column("approved_plan_revision", sa.String(64), nullable=True))

    # Earlier schemas made the key globally unique. Remove that constraint and
    # scope retries to their authenticated owner instead.
    for constraint in inspector.get_unique_constraints("runs"):
        if constraint.get("column_names") == ["idempotency_key"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "runs", type_="unique")
    unique_names = {
        (constraint.get("name"), tuple(constraint.get("column_names") or []))
        for constraint in sa.inspect(bind).get_unique_constraints("runs")
    }
    if ("uq_runs_user_id_idempotency_key", ("user_id", "idempotency_key")) not in unique_names:
        op.create_unique_constraint(
            "uq_runs_user_id_idempotency_key",
            "runs",
            ["user_id", "idempotency_key"],
        )

    now = sa.func.now()
    if not _table_exists(bind, "workflow_steps"):
        op.create_table(
            "workflow_steps",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column(
                "workflow_version_id",
                sa.String(128),
                sa.ForeignKey("workflow_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("task_key", sa.String(128), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("agent_ref", sa.String(128), nullable=True),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("input_mapping", sa.JSON(), nullable=False),
            sa.Column("expected_output_type", sa.String(32), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "workflow_version_id",
                "task_key",
                name="uq_workflow_steps_version_task_key",
            ),
        )
        op.create_index(
            "ix_workflow_steps_version_position",
            "workflow_steps",
            ["workflow_version_id", "position"],
        )

    if not _table_exists(bind, "workflow_step_dependencies"):
        op.create_table(
            "workflow_step_dependencies",
            sa.Column("step_id", sa.String(36), nullable=False),
            sa.Column("depends_on_step_id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
            sa.ForeignKeyConstraint(["step_id"], ["workflow_steps.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["depends_on_step_id"], ["workflow_steps.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("step_id", "depends_on_step_id"),
        )

    if not _table_exists(bind, "workflow_step_tools"):
        op.create_table(
            "workflow_step_tools",
            sa.Column("step_id", sa.String(36), nullable=False),
            sa.Column("tool_ref", sa.String(128), nullable=False),
            sa.Column("config_override", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
            sa.ForeignKeyConstraint(["step_id"], ["workflow_steps.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("step_id", "tool_ref"),
        )

    if not _table_exists(bind, "task_executions"):
        op.create_table(
            "task_executions",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("run_id", sa.String(36), nullable=False),
            sa.Column("workflow_step_id", sa.String(36), nullable=False),
            sa.Column("task_key", sa.String(128), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("agent_ref", sa.String(128), nullable=True),
            sa.Column("resolved_tool_ids", sa.JSON(), nullable=False),
            sa.Column("input_reference", sa.JSON(), nullable=False),
            sa.Column("result_id", sa.String(36), nullable=True),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
            sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["workflow_step_id"], ["workflow_steps.id"], ondelete="RESTRICT"
            ),
            sa.ForeignKeyConstraint(["result_id"], ["results.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", "workflow_step_id", name="uq_task_executions_run_step"),
        )
        op.create_index("ix_task_executions_run_status", "task_executions", ["run_id", "status"])

    _backfill_workflow_steps(bind)
    _backfill_task_executions(bind)
    _backfill_plan_revisions(bind)


def _backfill_plan_revisions(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text("SELECT run_id, plan, approval_status FROM runs")
    ).mappings()
    for row in rows:
        revision = _plan_revision(row["plan"])
        approved_revision = revision if row["approval_status"] == "approved" else None
        bind.execute(
            sa.text(
                "UPDATE runs SET plan_revision = :revision, "
                "approved_plan_revision = :approved_revision WHERE run_id = :run_id"
            ),
            {
                "revision": revision,
                "approved_revision": approved_revision,
                "run_id": row["run_id"],
            },
        )


def _backfill_workflow_steps(bind: sa.Connection) -> None:
    versions = bind.execute(
        sa.text("SELECT id, definition FROM workflow_versions ORDER BY workflow_id, version_number")
    ).mappings()
    for row in versions:
        version_id = str(row["id"])
        steps = _legacy_to_steps(row["definition"])
        keyed = {
            str(step.get("task_key") or f"step-{index}"): step
            for index, step in enumerate(steps, start=1)
        }
        id_by_key = {key: _step_id(version_id, key) for key in keyed}
        for position, (task_key, step) in enumerate(keyed.items()):
            exists = bind.execute(
                sa.text("SELECT 1 FROM workflow_steps WHERE id = :id"),
                {"id": id_by_key[task_key]},
            ).first()
            if exists:
                continue
            config = step.get("config") if isinstance(step.get("config"), dict) else {}
            bind.execute(
                sa.text(
                    "INSERT INTO workflow_steps "
                    "(id, workflow_version_id, task_key, name, description, agent_ref, config, "
                    "input_mapping, expected_output_type, position) "
                    "VALUES (:id, :version_id, :task_key, :name, :description, :agent_ref, "
                    ":config, :input_mapping, :output_type, :position)"
                ),
                {
                    "id": id_by_key[task_key],
                    "version_id": version_id,
                    "task_key": task_key,
                    "name": str(step.get("name") or task_key),
                    "description": str(step.get("description") or step.get("name") or task_key),
                    "agent_ref": str(step.get("agent_id") or step.get("agent_name") or "") or None,
                    "config": config,
                    "input_mapping": step.get("input_mapping") or step.get("input_mappings") or {},
                    "output_type": str(step.get("expected_output_type") or "raw_data"),
                    "position": int(step.get("position", position)),
                },
            )
            for dependency in step.get("dependencies") or []:
                dependency_key = str(dependency)
                if dependency_key in id_by_key:
                    bind.execute(
                        sa.text(
                            "INSERT INTO workflow_step_dependencies (step_id, depends_on_step_id) "
                            "VALUES (:step_id, :dependency_id) ON CONFLICT DO NOTHING"
                        ),
                        {"step_id": id_by_key[task_key], "dependency_id": id_by_key[dependency_key]},
                    )
            config_tools = config.get("tool_names", []) if isinstance(config, dict) else []
            for tool_ref in step.get("tool_ids") or step.get("tool_names") or config_tools:
                bind.execute(
                    sa.text(
                        "INSERT INTO workflow_step_tools (step_id, tool_ref, config_override) "
                        "VALUES (:step_id, :tool_ref, :config) ON CONFLICT DO NOTHING"
                    ),
                    {"step_id": id_by_key[task_key], "tool_ref": str(tool_ref), "config": {}},
                )


def _backfill_task_executions(bind: sa.Connection) -> None:
    runs = bind.execute(
        sa.text("SELECT run_id, workflow_version_id, plan FROM runs WHERE workflow_version_id IS NOT NULL")
    ).mappings()
    for run in runs:
        plan = run["plan"] if isinstance(run["plan"], list) else []
        step_rows = bind.execute(
            sa.text(
                "SELECT id, task_key, agent_ref FROM workflow_steps "
                "WHERE workflow_version_id = :version_id"
            ),
            {"version_id": run["workflow_version_id"]},
        ).mappings()
        steps = {step["task_key"]: step for step in step_rows}
        updated_plan = []
        for index, task in enumerate(plan, start=1):
            if not isinstance(task, dict):
                updated_plan.append(task)
                continue
            task_key = str(task.get("task_key") or task.get("id") or index)
            step = steps.get(task_key)
            if step is None:
                updated_plan.append(task)
                continue
            execution_id = str(
                task.get("task_execution_id") or _task_execution_id(str(run["run_id"]), task_key)
            )
            raw_status = str(task.get("status") or "pending")
            status = "completed" if raw_status in {"done", "partial"} else raw_status
            if status not in {"pending", "ready", "claimed", "running", "completed", "failed", "skipped", "cancelled", "interrupted"}:
                status = "pending"
            bind.execute(
                sa.text(
                    "INSERT INTO task_executions "
                    "(id, run_id, workflow_step_id, task_key, status, attempt_number, agent_ref, "
                    "resolved_tool_ids, input_reference) "
                    "VALUES (:id, :run_id, :step_id, :task_key, :status, 1, :agent_ref, "
                    ":tools, :input_reference) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": execution_id,
                    "run_id": run["run_id"],
                    "step_id": step["id"],
                    "task_key": task_key,
                    "status": status,
                    "agent_ref": task.get("agent_id") or task.get("node") or step["agent_ref"],
                    "tools": task.get("tool_names") or [],
                    "input_reference": task.get("input_mapping") or {},
                },
            )
            task["task_key"] = task_key
            task["task_execution_id"] = execution_id
            updated_plan.append(task)
        if updated_plan != plan:
            bind.execute(
                sa.text("UPDATE runs SET plan = :plan WHERE run_id = :run_id"),
                {"plan": updated_plan, "run_id": run["run_id"]},
            )


def downgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            "SELECT idempotency_key FROM runs WHERE idempotency_key IS NOT NULL "
            "GROUP BY idempotency_key HAVING COUNT(DISTINCT user_id) > 1 LIMIT 1"
        )
    ).first()
    if duplicate:
        raise RuntimeError(
            "Cannot downgrade owner-scoped idempotency: keys are duplicated across users."
        )
    op.drop_index("ix_task_executions_run_status", table_name="task_executions")
    op.drop_table("task_executions")
    op.drop_table("workflow_step_tools")
    op.drop_table("workflow_step_dependencies")
    op.drop_index("ix_workflow_steps_version_position", table_name="workflow_steps")
    op.drop_table("workflow_steps")
    op.drop_constraint("uq_runs_user_id_idempotency_key", "runs", type_="unique")
    op.create_unique_constraint("runs_idempotency_key_key", "runs", ["idempotency_key"])
    op.drop_column("runs", "approved_plan_revision")
    op.drop_column("runs", "plan_revision")
    op.drop_column("runs", "idempotency_fingerprint")
