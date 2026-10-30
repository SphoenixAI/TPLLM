"""Business code selects an explicit task; no routing policy lives here."""
from tpllm.decisions.registry import default_registry as decisions
from tpllm.intelligence_tasks.models import IntelligenceTask
from tpllm.intelligence_tasks.reasoning_registry import default_registry as reasoning


class IntelligenceTaskRegistry:
    def __init__(self, tasks=()):
        self._tasks = {}
        for task in tasks:
            self.register(task)

    def register(self, task):
        if task.definition_id in self._tasks:
            raise ValueError("Task version already registered")
        self._tasks[task.definition_id] = task.model_dump_json()

    def get(self, key):
        return IntelligenceTask.model_validate_json(self._tasks[key])

    def all(self):
        return tuple(self.get(k) for k in sorted(self._tasks))


def default_registry():
    tasks = [IntelligenceTask(task_id=q.decision_id, version=q.version, lane="decision", domain=q.domain,
        description=q.description, output_contract=q.question_type, evaluation_policy="probabilistic-calibration.v1")
        for q in decisions().all()]
    return IntelligenceTaskRegistry([*tasks, *reasoning().all()])
