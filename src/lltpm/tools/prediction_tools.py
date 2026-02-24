"""Risk prediction and evaluation tools — predict risks, generate quarterly reports."""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.prediction import PersonMetrics
from lltpm.workflows.risk_predictor import RiskPredictor


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

    def generate_evaluation(
        program_id: str,
        person: str,
        quarter: str = "",
        total_requests_received: int = 0,
        total_responses: int = 0,
        avg_response_hours: float = 0.0,
        high_priority_requests: int = 0,
        high_priority_responses: int = 0,
        ignored_messages: int = 0,
        commitments_made: int = 0,
        commitments_met: int = 0,
        unblocked_others: int = 0,
        was_blocker: int = 0,
    ) -> str:
        metrics = None
        if total_requests_received > 0 or commitments_made > 0:
            metrics = PersonMetrics(
                person=person,
                total_requests_received=total_requests_received,
                total_responses=total_responses,
                avg_response_hours=avg_response_hours,
                high_priority_requests=high_priority_requests,
                high_priority_responses=high_priority_responses,
                ignored_messages=ignored_messages,
                commitments_made=commitments_made,
                commitments_met=commitments_met,
                unblocked_others=unblocked_others,
                was_blocker=was_blocker,
            )

        evaluation = predictor.generate_evaluation(program_id, person, quarter, metrics)
        return predictor.render_evaluation(evaluation)

    def get_evaluation(
        program_id: str,
        person: str,
        quarter: str = "",
    ) -> str:
        evaluation = predictor.get_evaluation(program_id, person, quarter)
        if not evaluation:
            return json.dumps({"error": f"No evaluation found for {person}"})
        return predictor.render_evaluation(evaluation)

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
        {
            "name": "generate_evaluation",
            "description": (
                "Generate a quarterly evaluation report for a team member. "
                "Analyzes response rates, commitment follow-through, collaboration, "
                "and generates grades with narrative feedback."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "person": {"type": "string"},
                    "quarter": {"type": "string", "description": "e.g. 2026-Q1"},
                    "total_requests_received": {"type": "integer"},
                    "total_responses": {"type": "integer"},
                    "avg_response_hours": {"type": "number"},
                    "high_priority_requests": {"type": "integer"},
                    "high_priority_responses": {"type": "integer"},
                    "ignored_messages": {"type": "integer"},
                    "commitments_made": {"type": "integer"},
                    "commitments_met": {"type": "integer"},
                    "unblocked_others": {"type": "integer"},
                    "was_blocker": {"type": "integer"},
                },
                "required": ["program_id", "person"],
            },
            "executor": generate_evaluation,
        },
        {
            "name": "get_evaluation",
            "description": "Get a saved quarterly evaluation report for a team member.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "person": {"type": "string"},
                    "quarter": {"type": "string"},
                },
                "required": ["program_id", "person"],
            },
            "executor": get_evaluation,
        },
    ]
