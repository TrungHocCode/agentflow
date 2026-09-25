import React from 'react';
import { Bot, ChevronDown } from 'lucide-react';
import TaskStatusBadge from './TaskStatusBadge';

const FINISHED_STATUSES = new Set([
  'done', 'success', 'completed', 'partial', 'failed', 'skipped', 'cancelled', 'interrupted'
]);

function getTaskId(task, index) {
  return String(task?.id ?? `task-${index + 1}`);
}

function getDependencies(task) {
  return Array.isArray(task?.dependencies)
    ? task.dependencies.map(String)
    : [];
}

function groupTasksByStage(tasks) {
  const taskIds = new Set(tasks.map(getTaskId));
  const remaining = tasks.map((task, index) => ({ task, index, id: getTaskId(task, index) }));
  const stageById = new Map();
  let attempts = 0;

  while (remaining.length > 0 && attempts <= tasks.length) {
    const ready = remaining.filter(({ task }) =>
      getDependencies(task).every(id => !taskIds.has(id) || stageById.has(id))
    );
    const batch = ready.length > 0 ? ready : [...remaining];

    for (const { task, id } of batch) {
      const dependencyStages = getDependencies(task)
        .map(dependencyId => stageById.get(dependencyId))
        .filter(stage => Number.isInteger(stage));
      stageById.set(id, dependencyStages.length > 0 ? Math.max(...dependencyStages) + 1 : 0);
    }

    const batchIds = new Set(batch.map(item => item.id));
    for (let index = remaining.length - 1; index >= 0; index -= 1) {
      if (batchIds.has(remaining[index].id)) remaining.splice(index, 1);
    }
    attempts += 1;
  }

  const groups = new Map();
  tasks.forEach((task, index) => {
    const stage = stageById.get(getTaskId(task, index)) ?? 0;
    if (!groups.has(stage)) groups.set(stage, []);
    groups.get(stage).push({ task, index });
  });

  return [...groups.entries()]
    .sort(([left], [right]) => left - right)
    .map(([stage, nodes]) => ({ stage, nodes }));
}

export default function WorkflowProgressGraph({ tasks, runStatus }) {
  if (!tasks?.length) return null;

  const stages = groupTasksByStage(tasks);
  const displayNumberById = new Map(tasks.map((task, index) => [getTaskId(task, index), index + 1]));
  const finishedCount = tasks.filter(task => FINISHED_STATUSES.has(String(task?.status).toLowerCase())).length;
  const progress = Math.round((finishedCount / tasks.length) * 100);
  const isRunStarted = Boolean(runStatus);

  return (
    <div className="workflow-progress">
      <div className="workflow-progress__summary">
        <span className="workflow-progress__step-count">
          {isRunStarted
            ? `${finishedCount} / ${tasks.length} bước đã kết thúc`
            : `${tasks.length} bước trong kế hoạch`}
        </span>
        {stages.some(stage => stage.nodes.length > 1) && (
          <span className="workflow-progress__parallel-hint">Các node cùng hàng có thể chạy song song</span>
        )}
      </div>

      {isRunStarted && (
        <div
          className="workflow-progress__track"
          role="progressbar"
          aria-label="Tiến độ workflow"
          aria-valuetext={`${finishedCount} trên ${tasks.length} bước đã kết thúc`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
        >
          <span style={{ width: `${progress}%` }} />
        </div>
      )}

      <div className="workflow-progress__stages">
        {stages.map((stage, stageIndex) => (
          <React.Fragment key={stage.stage}>
            {stageIndex > 0 && (
              <div className="workflow-progress__connector" aria-hidden="true">
                <span />
                <ChevronDown size={17} />
              </div>
            )}
            <div
              className="workflow-progress__stage"
              role="group"
              aria-label={`Giai đoạn ${stageIndex + 1}`}
            >
              {stages.length > 1 && (
                <span className="workflow-progress__stage-label">Giai đoạn {stageIndex + 1}</span>
              )}
              <div className="workflow-progress__nodes">
                {stage.nodes.map(({ task, index }) => {
                  const status = String(task?.status || 'pending').toLowerCase();
                  const nodeStatus = {
                    success: 'done',
                    completed: 'done',
                    cancelled: 'skipped',
                    interrupted: 'partial'
                  }[status] || status;
                  const dependencies = getDependencies(task);
                  const dependencyNumbers = dependencies.map(id => displayNumberById.get(id) ?? id);
                  const agentName = task?.node && task.node !== 'worker' ? task.node : 'Research agent';

                  return (
                    <article
                      key={getTaskId(task, index)}
                      className={`workflow-node workflow-node--${nodeStatus}`}
                      aria-busy={status === 'running'}
                    >
                      <div className="workflow-node__heading">
                        <span className="workflow-node__number" aria-hidden="true">{index + 1}</span>
                        <TaskStatusBadge status={status} />
                      </div>
                      <p className="workflow-node__description">
                        {task?.description || `Bước ${index + 1}`}
                      </p>
                      <div className="workflow-node__meta">
                        <span><Bot size={13} aria-hidden="true" /> {agentName}</span>
                        {dependencies.length > 0 && (
                          <span className="workflow-node__dependencies">
                            Sau bước {dependencyNumbers.join(', ')}
                          </span>
                        )}
                      </div>
                      {status === 'running' && (
                        <div className="workflow-node__activity" aria-hidden="true">
                          <span />
                        </div>
                      )}
                    </article>
                  );
                })}
              </div>
            </div>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}
