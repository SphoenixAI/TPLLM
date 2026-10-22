"""Risk prediction tools.

An earlier release removed `generate_evaluation` and `get_evaluation`
from this module. They let the agent produce a per-person quarterly performance
review — responsiveness, reliability and collaboration letter grades,
computed in part from how often someone "was the blocker" — and they
were the only place the model could be asked to grade a colleague.

The structural measures the rest of the system exposes answer the
question those tools were reached for and answer it better: a team that
looks slow because four projects share one approval authority is a
finding about the approval authority.
"""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.risk_predictor import RiskPredictor


def get_prediction_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    predictor = RiskPredictor(store)

    def predict_risks(program_id: str) -> str:
        predictions = predictor.predict_risks(program_id)
        return json.dumps({
            "count": len(predictions),
            "predictions": [
                {
                    "id": p.id,
                    "type": p.signal_type.value,
                    "title": p.title,
                    "confidence": p.confidence.value,
                    "probability": round(p.probability, 2),
                    "impact": p.impact,
                    "affected_people": p.affected_people,
                    "actions": p.recommended_actions,
                }
                for p in predictions
            ],
        })

    def get_prediction_summary(program_id: str) -> str:
        return predictor.get_prediction_summary(program_id)

    return [
        {
            "name": "predict_risks",
            "description": (
                "Run risk prediction models against all program data. "
                "Analyzes milestones, delegations, engagement, scope, and nudge patterns "
                "to predict risks before they become blockers."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": predict_risks,
        },
        {
            "name": "get_prediction_summary",
            "description": "Get a formatted summary of current risk predictions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": get_prediction_summary,
        },
    ]
