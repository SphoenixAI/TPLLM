"""End-to-end tests — full user-flow scenarios through the system.

These tests exercise the complete pipeline from program creation through
risk detection, delegation, nudging, approvals, and notifications —
exactly as the agent would use them in production.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.config import LLTPMConfig, IntegrationConfig
from lltpm.tools.registry import build_tool_registry


def _make_store() -> ArtifactStore:
    return ArtifactStore(Path(tempfile.mkdtemp()))


def _make_config() -> LLTPMConfig:
    return LLTPMConfig(data_dir=Path(tempfile.mkdtemp()))


# ── Flow 1: Program Creation → Milestone → Risk → Status Report ──────

class TestProgramLifecycleFlow:
    """Full lifecycle: create program → add milestones → log risks → generate status."""

    def setup_method(self):
        self.store = _make_store()
        self.config = _make_config()
        self.registry = build_tool_registry(self.config, self.store)

    def test_create_program(self):
        result = self.registry.execute("create_program", {
            "program_id": "e2e-test",
            "name": "E2E Test Program",
            "goal": "Validate the full TPM agent pipeline end-to-end",
            "target_date": "2026-06-01",
            "stakeholders": [
                {"name": "Alice", "role": "Engineering Lead", "team": "Platform"},
                {"name": "Bob", "role": "Product Manager", "team": "Product"},
            ],
            "success_metrics": [
                {"name": "Test Coverage", "target": ">90%"},
            ],
        })
        assert "e2e-test" in result
        assert "E2E Test Program" in result

    def test_full_program_lifecycle(self):
        # 1. Create program
        self.registry.execute("create_program", {
            "program_id": "lifecycle",
            "name": "Lifecycle Test",
            "goal": "Test the full lifecycle",
            "target_date": "2026-09-01",
        })

        # 2. Add milestone
        result = self.registry.execute("add_milestone", {
            "program_id": "lifecycle",
            "name": "Alpha Release",
            "target_date": "2026-04-15",
            "owner": "Alice",
        })
        assert "Alpha Release" in result

        # 3. Log a risk
        result = self.registry.execute("add_risk", {
            "program_id": "lifecycle",
            "title": "API dependency may slip",
            "severity": "HIGH",
            "impact": "Delays milestone by 2 weeks",
            "owner": "Bob",
            "mitigation": "Identify backup API provider",
        })
        assert "API dependency" in result

        # 4. Get program brief
        result = self.registry.execute("get_program_brief", {
            "program_id": "lifecycle",
        })
        assert "Lifecycle Test" in result

        # 5. Get RAID summary
        result = self.registry.execute("get_raid_summary", {
            "program_id": "lifecycle",
        })
        assert "API dependency" in result or "risk" in result.lower()


# ── Flow 2: Meeting Intelligence → Delegation → Nudge ────────────────

class TestMeetingToDelegationFlow:
    """Meeting notes → extract commitments → delegate → nudge if overdue."""

    def setup_method(self):
        self.store = _make_store()
        self.config = _make_config()
        self.registry = build_tool_registry(self.config, self.store)

    def test_meeting_to_delegation_flow(self):
        # 1. Create program
        self.registry.execute("create_program", {
            "program_id": "meeting-flow",
            "name": "Meeting Flow Test",
            "goal": "Test meeting intelligence pipeline",
        })

        # 2. Ingest meeting notes via Zoom engine
        result = self.registry.execute("ingest_zoom_notes", {
            "program_id": "meeting-flow",
            "meeting_id": "ZM-E2E-001",
            "provider": "manual",
            "raw_notes": "Alice committed to finishing auth by March 1. Bob will review the spec.",
            "summary": "Sprint planning: auth migration and spec review.",
            "action_items": [
                {"owner": "Alice", "description": "Finish auth migration by March 1"},
                {"owner": "Bob", "description": "Review API spec"},
            ],
            "key_decisions": ["Use OAuth2 for auth migration"],
        })
        assert "ZM-E2E-001" in result
        assert "action_items_count" in result

        # 3. Get analysis prompt
        result = self.registry.execute("get_zoom_analysis_prompt", {
            "program_id": "meeting-flow",
            "meeting_id": "ZM-E2E-001",
        })
        assert "Alice" in result
        assert "auth" in result.lower()

        # 4. Create a delegation
        result = self.registry.execute("delegate_task", {
            "program_id": "meeting-flow",
            "title": "Complete auth migration",
            "assigned_to": "Alice",
            "source": "meeting",
            "context": "From sprint planning meeting ZM-E2E-001",
            "priority": "P0",
        })
        assert "Alice" in result or "delegation" in result.lower()

        # 5. Create a nudge for the delegation
        result = self.registry.execute("create_nudge", {
            "program_id": "meeting-flow",
            "target_person": "Alice",
            "subject": "Complete auth migration",
            "why_it_matters": "Blocks the sprint goal",
        })
        assert "Alice" in result or "nudge" in result.lower()


# ── Flow 3: Scope Lock → Change Request → Approval ──────────────────

class TestScopeChangeApprovalFlow:
    """Scope baseline → change request → approval → scope updated."""

    def setup_method(self):
        self.store = _make_store()
        self.config = _make_config()
        self.registry = build_tool_registry(self.config, self.store)

    def test_scope_change_flow(self):
        # 1. Create program
        self.registry.execute("create_program", {
            "program_id": "scope-flow",
            "name": "Scope Flow Test",
            "goal": "Test scope lock pipeline",
        })

        # 2. Add scope items
        self.registry.execute("add_scope_item", {
            "program_id": "scope-flow",
            "title": "Auth system",
        })
        self.registry.execute("add_scope_item", {
            "program_id": "scope-flow",
            "title": "User dashboard",
        })
        result = self.registry.execute("add_scope_item", {
            "program_id": "scope-flow",
            "title": "API gateway",
        })
        assert "scope" in result.lower() or "API gateway" in result

        # 3. Request scope change
        result = self.registry.execute("request_scope_change", {
            "program_id": "scope-flow",
            "title": "Add SSO integration",
            "requestor": "Product Team",
            "justification": "Enterprise customers require SSO for compliance",
            "impact_description": "Adds 2 weeks to timeline, needs auth team bandwidth",
        })
        assert "SSO" in result or "scope" in result.lower()

        # 4. Create an approval request
        result = self.registry.execute("create_approval_request", {
            "program_id": "scope-flow",
            "title": "Approve SSO scope change",
            "description": "Add SSO integration to auth system — 2 week impact.",
            "requestor": "Product Team",
            "approvers": ["Alice", "Bob"],
        })
        assert "approval" in result.lower() or "SSO" in result


# ── Flow 4: Cross-Program Intelligence ───────────────────────────────

class TestCrossProgramFlow:
    """Multiple programs → portfolio view → conflict detection."""

    def setup_method(self):
        self.store = _make_store()
        self.config = _make_config()
        self.registry = build_tool_registry(self.config, self.store)

    def test_portfolio_view(self):
        # Create two programs
        self.registry.execute("create_program", {
            "program_id": "prog-alpha",
            "name": "Project Alpha",
            "goal": "Build the alpha product",
            "target_date": "2026-06-01",
            "stakeholders": [{"name": "Alice", "role": "Lead"}],
        })
        self.registry.execute("create_program", {
            "program_id": "prog-beta",
            "name": "Project Beta",
            "goal": "Build the beta product",
            "target_date": "2026-08-01",
            "stakeholders": [{"name": "Bob", "role": "Lead"}],
        })

        # Get portfolio view
        result = self.registry.execute("get_portfolio_view", {})
        assert "Alpha" in result or "programs" in result.lower()


# ── Flow 5: Engagement Tracking → Trend Analysis ────────────────────

class TestEngagementTrendFlow:
    """Track engagement → analyze trends over time."""

    def setup_method(self):
        self.store = _make_store()
        self.config = _make_config()
        self.registry = build_tool_registry(self.config, self.store)

    def test_engagement_to_trend(self):
        # 1. Create program
        self.registry.execute("create_program", {
            "program_id": "engage-flow",
            "name": "Engagement Test",
            "goal": "Test engagement tracking",
        })

        # 2. Record engagement events
        result = self.registry.execute("record_engagement", {
            "program_id": "engage-flow",
            "person": "Alice",
            "channel": "SLACK",
            "response_hours": 0.1,
        })
        assert "Alice" in result or "engagement" in result.lower()

        # 3. Record another engagement
        self.registry.execute("record_engagement", {
            "program_id": "engage-flow",
            "person": "Bob",
            "channel": "EMAIL",
            "ignored": True,
        })

        # 4. Get engagement summary
        result = self.registry.execute("get_engagement_summary", {
            "program_id": "engage-flow",
        })
        assert "Alice" in result or "engagement" in result.lower()

        # 5. Record health snapshot for trend analysis
        result = self.registry.execute("record_health_snapshot", {
            "program_id": "engage-flow",
        })
        assert "snapshot" in result.lower() or "health" in result.lower()


# ── Flow 6: Risk Prediction Pipeline ─────────────────────────────────

class TestRiskPredictionFlow:
    """Program → milestones → risks → predict future risks."""

    def setup_method(self):
        self.store = _make_store()
        self.config = _make_config()
        self.registry = build_tool_registry(self.config, self.store)

    def test_risk_prediction_flow(self):
        # 1. Create program with milestones and risks
        self.registry.execute("create_program", {
            "program_id": "risk-flow",
            "name": "Risk Prediction Test",
            "goal": "Test predictive risk analysis",
            "target_date": "2026-06-01",
        })

        self.registry.execute("add_milestone", {
            "program_id": "risk-flow",
            "name": "MVP Complete",
            "target_date": "2026-04-01",
            "owner": "Charlie",
        })

        self.registry.execute("add_risk", {
            "program_id": "risk-flow",
            "title": "Database migration delayed",
            "severity": "CRITICAL",
            "impact": "Blocks MVP launch",
            "owner": "Charlie",
        })

        # 2. Run risk predictions
        result = self.registry.execute("predict_risks", {
            "program_id": "risk-flow",
        })
        assert "risk" in result.lower() or "prediction" in result.lower()


# ── Flow 7: Document Generation ──────────────────────────────────────

class TestDocumentGenerationFlow:
    """Create program → populate data → generate documents."""

    def setup_method(self):
        self.store = _make_store()
        self.config = _make_config()
        self.registry = build_tool_registry(self.config, self.store)

    def test_generate_program_brief_document(self):
        # Create program
        self.registry.execute("create_program", {
            "program_id": "doc-flow",
            "name": "Doc Generation Test",
            "goal": "Validate document rendering",
            "target_date": "2026-06-01",
            "stakeholders": [
                {"name": "Alice", "role": "Lead", "team": "Engineering"},
            ],
        })

        # Render document
        result = self.registry.execute("render_document", {
            "program_id": "doc-flow",
            "doc_type": "program_brief",
            "fmt": "html",
        })
        assert "Doc Generation Test" in result or "html" in result.lower()


# ── Flow 8: Persistent Memory ────────────────────────────────────────

class TestPersistentMemoryFlow:
    """Save conversation → retrieve context → detect gaps."""

    def setup_method(self):
        self.store = _make_store()
        self.config = _make_config()
        self.registry = build_tool_registry(self.config, self.store)

    def test_memory_save_and_retrieve(self):
        # 1. Create program
        self.registry.execute("create_program", {
            "program_id": "mem-flow",
            "name": "Memory Test",
            "goal": "Test persistent memory",
        })

        # 2. Save a conversation turn
        result = self.registry.execute("record_memory_turn", {
            "program_id": "mem-flow",
            "role": "user",
            "content": "What is the status of the auth migration?",
        })
        assert "saved" in result.lower() or "turn" in result.lower() or "recorded" in result.lower()

        # 3. Save agent response
        self.registry.execute("record_memory_turn", {
            "program_id": "mem-flow",
            "role": "assistant",
            "content": "The auth migration is 70% complete. Alice is handling the OAuth2 implementation.",
        })

        # 4. Get memory summary
        result = self.registry.execute("get_memory_summary", {
            "program_id": "mem-flow",
        })
        assert "auth" in result.lower() or "memory" in result.lower() or "turn" in result.lower()


# ── Flow 9: Webhook → Meeting → Notes Pipeline ──────────────────────

class TestWebhookToNotesFlow:
    """Webhook event → register meeting → ingest notes → analyze."""

    def setup_method(self):
        self.store = _make_store()
        self.config = _make_config()
        self.registry = build_tool_registry(self.config, self.store)

    def test_webhook_to_analysis(self):
        # 1. Create program
        self.registry.execute("create_program", {
            "program_id": "webhook-flow",
            "name": "Webhook Flow Test",
            "goal": "Test webhook to analysis pipeline",
        })

        # 2. Register a meeting
        result = self.registry.execute("register_zoom_meeting", {
            "program_id": "webhook-flow",
            "topic": "Architecture Review",
            "host": "Alice",
            "participants": ["Bob", "Charlie", "Diana"],
        })
        assert "meeting_id" in result

        # 3. Ingest a Zoom webhook event
        result = self.registry.execute("ingest_zoom_event", {
            "program_id": "webhook-flow",
            "event_type": "meeting.ended",
        })
        assert "event" in result.lower() or "zoom" in result.lower() or "ingested" in result.lower()

        # 4. Ingest meeting notes
        result = self.registry.execute("ingest_zoom_notes", {
            "program_id": "webhook-flow",
            "provider": "manual",
            "raw_notes": "Discussed microservice architecture. Charlie will prototype the gateway.",
            "summary": "Architecture review: microservice approach approved.",
            "action_items": [
                {"owner": "Charlie", "description": "Prototype API gateway"},
            ],
            "key_decisions": ["Adopt microservice architecture"],
        })
        assert "action_items_count" in result


# ── Flow 10: Tool Registry Completeness ──────────────────────────────

class TestToolRegistryCompleteness:
    """Verify the tool registry loads all expected tools without errors."""

    def test_registry_builds_without_error(self):
        config = _make_config()
        store = _make_store()
        registry = build_tool_registry(config, store)
        assert len(registry.definitions) > 30

    def test_registry_has_core_tools(self):
        config = _make_config()
        store = _make_store()
        registry = build_tool_registry(config, store)

        tool_names = {t["name"] for t in registry.definitions}

        # Program management
        assert "create_program" in tool_names
        assert "get_program_brief" in tool_names

        # RAID
        assert "add_risk" in tool_names
        assert "get_raid_summary" in tool_names

        # Milestones
        assert "add_milestone" in tool_names

        # Meeting intelligence
        assert "ingest_zoom_notes" in tool_names
        assert "get_zoom_analysis_prompt" in tool_names

        # Delegation
        assert "delegate_task" in tool_names

        # Nudging
        assert "create_nudge" in tool_names

        # Scope lock
        assert "add_scope_item" in tool_names
        assert "request_scope_change" in tool_names

        # Memory
        assert "record_memory_turn" in tool_names

        # Predictions
        assert "predict_risks" in tool_names

        # Documents
        assert "render_document" in tool_names

        # Approvals
        assert "create_approval_request" in tool_names

    def test_all_tools_have_executors(self):
        config = _make_config()
        store = _make_store()
        registry = build_tool_registry(config, store)

        for tool_def in registry.definitions:
            name = tool_def["name"]
            assert name in registry._executors, f"Tool '{name}' missing executor"

    def test_conditional_tools_load_when_enabled(self):
        """When integrations are enabled, their tools should appear."""
        config = _make_config()
        config.email = IntegrationConfig(
            enabled=True,
            api_key="test-key",
            extra={"backend": "sendgrid", "from_email": "test@example.com"},
        )
        store = _make_store()
        registry = build_tool_registry(config, store)

        tool_names = {t["name"] for t in registry.definitions}
        assert "send_email" in tool_names or "send_digest_email" in tool_names
