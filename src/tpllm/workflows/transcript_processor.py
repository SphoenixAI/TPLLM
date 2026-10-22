"""Meeting Transcript Processor — detect ambiguity, vague commitments, and gaps.

Scans meeting transcripts (or AI meeting tool notes) for:
- Vague commitments ("soon", "ASAP", "when I get to it")
- Missing owners ("someone should...", "we need to...")
- Unclear scope ("maybe we could...", "it depends...")
- Timeline ambiguity ("by end of quarter", "in a few weeks")
- Unresolved disagreements
- Action items without deadlines

The processor produces clarification requests that can be sent
to specific attendees with quick-response options.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


@dataclass
class AmbiguityFlag:
    """A detected ambiguity in meeting content."""

    category: str          # "vague_commitment", "no_owner", "unclear_timeline", etc.
    severity: str          # LOW, MEDIUM, HIGH
    text: str              # The ambiguous text
    context: str           # Surrounding context
    suggested_clarification: str  # What to ask
    suggested_owner: str = ""     # Who to ask
    quick_options: list[str] = field(default_factory=list)


@dataclass
class ClarificationRequest:
    """A request to clarify an ambiguity, ready to send."""

    target_person: str
    subject: str
    context: str
    question: str
    quick_options: list[str] = field(default_factory=list)
    urgency: str = "MEDIUM"
    source: str = ""       # Which meeting this came from


# Patterns that indicate vague commitments
VAGUE_COMMITMENT_PATTERNS = [
    (r'\b(soon|asap|when I get to it|when I can|eventually)\b', "vague_timeline"),
    (r'\b(probably|maybe|might|could potentially|hopefully)\b', "uncertain_commitment"),
    (r'\b(someone should|we need to|we should|somebody needs)\b', "no_owner"),
    (r'\b(a few weeks|couple of days|some time|a while|not sure when)\b', "unclear_timeline"),
    (r'\b(it depends|depends on|we\'ll see|TBD|to be determined)\b', "conditional"),
    (r'\b(I think|I guess|I assume|not sure if)\b', "low_confidence"),
    (r'\b(let\'s circle back|revisit later|park this|table this)\b', "deferred_untracked"),
    (r'\b(end of quarter|next quarter|sometime next|early next)\b', "vague_date"),
]

# Category to severity mapping
CATEGORY_SEVERITY = {
    "vague_timeline": "MEDIUM",
    "uncertain_commitment": "LOW",
    "no_owner": "HIGH",
    "unclear_timeline": "MEDIUM",
    "conditional": "MEDIUM",
    "low_confidence": "LOW",
    "deferred_untracked": "HIGH",
    "vague_date": "MEDIUM",
}

# Category to suggested quick options
CATEGORY_OPTIONS = {
    "vague_timeline": ["This week", "Next week", "End of sprint", "Need to discuss timeline"],
    "uncertain_commitment": ["I'll commit to this", "Can't commit yet", "Need more info", "Not my area"],
    "no_owner": ["I'll own this", "Suggest: [name]", "Need team discussion", "Backlog it"],
    "unclear_timeline": ["By [specific date]", "End of sprint", "Need to scope first", "Unknown"],
    "conditional": ["Dependency is met", "Dependency is blocked", "Need to clarify dependency", "Alternative plan needed"],
    "low_confidence": ["I'm confident now", "Still uncertain", "Need more data", "Let's spike on it"],
    "deferred_untracked": ["Still relevant — add to backlog", "No longer needed", "Discuss next meeting", "Create action item"],
    "vague_date": ["By [specific date]", "End of month", "End of quarter", "Need to align on date"],
}


class TranscriptProcessor:
    """Process meeting transcripts to detect ambiguity and generate clarifications."""

    def analyze(
        self,
        content: str,
        attendees: list[str] | None = None,
        meeting_title: str = "",
    ) -> list[AmbiguityFlag]:
        """Scan transcript content for ambiguity patterns."""
        flags: list[AmbiguityFlag] = []

        lines = content.split("\n")
        for i, line in enumerate(lines):
            for pattern, category in VAGUE_COMMITMENT_PATTERNS:
                matches = re.finditer(pattern, line, re.IGNORECASE)
                for match in matches:
                    # Get context (surrounding lines)
                    start = max(0, i - 1)
                    end = min(len(lines), i + 2)
                    context = "\n".join(lines[start:end])

                    # Try to identify who said it
                    speaker = self._detect_speaker(line, attendees)

                    flags.append(AmbiguityFlag(
                        category=category,
                        severity=CATEGORY_SEVERITY.get(category, "MEDIUM"),
                        text=match.group(0),
                        context=context,
                        suggested_clarification=self._get_clarification(category, match.group(0), line),
                        suggested_owner=speaker,
                        quick_options=CATEGORY_OPTIONS.get(category, []),
                    ))

        return flags

    def generate_clarifications(
        self,
        flags: list[AmbiguityFlag],
        meeting_title: str = "",
    ) -> list[ClarificationRequest]:
        """Convert ambiguity flags into sendable clarification requests."""
        requests: list[ClarificationRequest] = []

        # Group by person
        by_person: dict[str, list[AmbiguityFlag]] = {}
        for flag in flags:
            key = flag.suggested_owner or "UNASSIGNED"
            by_person.setdefault(key, []).append(flag)

        for person, person_flags in by_person.items():
            if person == "UNASSIGNED":
                # Can't clarify without a target — these need manual assignment
                for flag in person_flags:
                    requests.append(ClarificationRequest(
                        target_person="",
                        subject=f"Unowned item from {meeting_title}",
                        context=flag.context,
                        question=flag.suggested_clarification,
                        quick_options=flag.quick_options,
                        urgency=flag.severity,
                        source=meeting_title,
                    ))
                continue

            # Batch flags for the same person into one request
            if len(person_flags) == 1:
                flag = person_flags[0]
                requests.append(ClarificationRequest(
                    target_person=person,
                    subject=f"Clarification from {meeting_title}",
                    context=flag.context,
                    question=flag.suggested_clarification,
                    quick_options=flag.quick_options,
                    urgency=flag.severity,
                    source=meeting_title,
                ))
            else:
                # Multiple items — batch them
                questions = []
                all_options = set()
                max_urgency = "LOW"
                urgency_order = ["LOW", "MEDIUM", "HIGH"]

                for flag in person_flags:
                    questions.append(f"- {flag.suggested_clarification}")
                    all_options.update(flag.quick_options[:2])
                    if urgency_order.index(flag.severity) > urgency_order.index(max_urgency):
                        max_urgency = flag.severity

                requests.append(ClarificationRequest(
                    target_person=person,
                    subject=f"{len(person_flags)} clarifications from {meeting_title}",
                    context=f"From: {meeting_title}",
                    question="\n".join(questions),
                    quick_options=list(all_options)[:4],
                    urgency=max_urgency,
                    source=meeting_title,
                ))

        return requests

    def get_summary(self, flags: list[AmbiguityFlag]) -> str:
        """Render a summary of detected ambiguities."""
        if not flags:
            return "No ambiguities detected — meeting content is clear."

        lines = [f"# Transcript Analysis — {len(flags)} ambiguities detected"]

        by_severity = {"HIGH": [], "MEDIUM": [], "LOW": []}
        for f in flags:
            by_severity.get(f.severity, by_severity["LOW"]).append(f)

        for sev in ["HIGH", "MEDIUM", "LOW"]:
            items = by_severity[sev]
            if items:
                lines.append(f"\n## {sev} ({len(items)})")
                for f in items:
                    owner = f" — Ask: {f.suggested_owner}" if f.suggested_owner else ""
                    lines.append(
                        f"- [{f.category}] \"{f.text}\"{owner}\n"
                        f"  → {f.suggested_clarification}"
                    )

        return "\n".join(lines)

    def _detect_speaker(self, line: str, attendees: list[str] | None) -> str:
        """Try to detect who is speaking from the line."""
        if not attendees:
            return ""
        # Common transcript format: "Name: text" or "Name said..."
        for name in attendees:
            if line.strip().lower().startswith(f"{name.lower()}:"):
                return name
            if line.strip().lower().startswith(f"{name.lower()} said"):
                return name
        return ""

    def _get_clarification(self, category: str, matched_text: str, full_line: str) -> str:
        """Generate a human-readable clarification question."""
        templates = {
            "vague_timeline": f'"{matched_text}" — can you provide a specific date?',
            "uncertain_commitment": f'"{matched_text}" — is this a firm commitment or still being evaluated?',
            "no_owner": f'"{matched_text}" — who specifically will own this?',
            "unclear_timeline": f'"{matched_text}" — what specific date or sprint are we targeting?',
            "conditional": f'"{matched_text}" — what needs to be true for this to move forward?',
            "low_confidence": f'"{matched_text}" — what would increase confidence? What data do you need?',
            "deferred_untracked": f'"{matched_text}" — should we create a tracked action item for this?',
            "vague_date": f'"{matched_text}" — can you commit to a specific date?',
        }
        return templates.get(category, f'Please clarify: "{matched_text}"')
