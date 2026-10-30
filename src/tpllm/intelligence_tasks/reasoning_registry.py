from tpllm.intelligence_tasks.models import IntelligenceTask
from tpllm.intelligence_tasks.schemas import SCHEMAS


class ReasoningTaskRegistry:
    def __init__(self):
        self._definitions = {}

    def register(self, task):
        if task.lane != "reasoning" or task.definition_id in self._definitions:
            raise ValueError("Reasoning schema identity is immutable; use a new version")
        self._definitions[task.definition_id] = task.model_dump_json()

    def get(self, key):
        return IntelligenceTask.model_validate_json(self._definitions[key])

    def all(self):
        return tuple(self.get(k) for k in sorted(self._definitions))


def default_registry():
    registry = ReasoningTaskRegistry()
    purposes = {
        "root_cause_analysis": "Synthesize primary and alternative hypotheses, including interacting causes and contradictions.",
        "operational_summary": "Summarize material operational changes, dependencies and unresolved questions.",
        "dependency_analysis": "Describe evidenced dependency paths, bottlenecks and possible downstream impacts.",
        "recovery_options": "Describe possible recovery options and authority requirements for human review; execute nothing.",
        "insight_explanation": "Explain the supplied existing TPLLM insight. This is a new synthesis, never Jev chain-of-thought.",
    }
    for key, model in SCHEMAS.items():
        name = key.split(".")[1]
        registry.register(IntelligenceTask(task_id="reasoning."+name, version=1, lane="reasoning",
            domain="operations", description=purposes[name], output_contract=model.__name__,
            required_context=("evidence", "insight") if name == "insight_explanation" else ("evidence",),
            output_schema=model.model_json_schema(), evaluation_policy="structured-artifact-review.v1",
            provider_constraints=("native_json_schema", "no_tools")))
    return registry
