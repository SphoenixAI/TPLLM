"""Scoped collection for the product loop. Provider reads never execute actions.

The same adapters accept injected readers in tests. Bounds, missing timestamps,
partial reads and unavailable notes remain visible rather than becoming facts.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from pydantic import BaseModel, Field

from tpllm.integrations.envelopes import redact_secret_text


def utc(value):
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("A source timestamp is required")
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:24]


def clean(value):
    """Redact nested source text before it reaches any local persistence."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return redact_secret_text(value)[0] if isinstance(value, str) else value


class SourceRecord(BaseModel):
    provider: str
    scope: str
    project_id: str
    source_id: str
    kind: str
    title: str
    text: str = ""
    observed_at: datetime
    url: str = ""
    data: dict = Field(default_factory=dict)

    @property
    def key(self):
        return f"{self.provider}:{self.scope}:{self.source_id}"

    @property
    def version(self):
        return digest(self.model_dump(mode="json"))


@dataclass
class Collection:
    records: list[SourceRecord] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def add(self, **values):
        try:
            values["observed_at"] = utc(values.get("observed_at"))
            self.records.append(SourceRecord(**clean(values)))
        except (ValueError, TypeError):
            self.limitations.append(f"{values.get('source_id', 'Record')}: missing or invalid source time; excluded.")


def collect_github(cfg, scope, project_id, *, reader=None):
    from tpllm.integrations.github_reader import UrllibGitHubReader
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", scope):
        raise ValueError("GitHub scope must be owner/repository")
    reader = reader or UrllibGitHubReader(token=cfg.api_key)
    batch = Collection()
    root = f"repos/{scope}"
    def listing(path, key=None):
        items = []
        for page in range(1, 4):
            try:
                response = reader.get(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
                rows = response.get(key, []) if key else response
                if not isinstance(rows, list):
                    raise ValueError("Unexpected source shape")
                items.extend(rows)
                if len(rows) < 100:
                    return items
            except Exception as exc:
                batch.limitations.append(f"{path}: unavailable ({type(exc).__name__}).")
                return items
        batch.limitations.append(f"{path}: bounded at 300 records; additional pages may exist.")
        return items
    pulls = listing(f"{root}/pulls?state=all&sort=updated&direction=desc")
    for index, pr in enumerate(pulls):
        number = pr.get("number")
        if not isinstance(number, int):
            continue
        detail, files, reviews, checks = pr, [], [], []
        coverage = []
        limitations_before = len(batch.limitations)
        if index < 5:
            try:
                detail = reader.get(f"{root}/pulls/{number}")
                head = detail.get("head", {}).get("sha", "")
                files = listing(f"{root}/pulls/{number}/files")
                reviews = listing(f"{root}/pulls/{number}/reviews")
                if head:
                    checks = listing(f"{root}/commits/{quote(head, safe='')}/check-runs", "check_runs")
                latest = reader.get(f"{root}/pulls/{number}")
                if latest.get("head", {}).get("sha") != head:
                    coverage.append("Head changed during collection; diffs and checks need another read.")
            except Exception as exc:
                coverage.append(f"PR details unavailable ({type(exc).__name__}).")
        else:
            coverage.append("Diffs, reviews and checks not read: five most recently updated PRs per pass.")
        coverage.extend(batch.limitations[limitations_before:])
        latest_reviews = {}
        for review in sorted(reviews, key=lambda x: x.get("submitted_at") or ""):
            login = review.get("user", {}).get("login")
            if login and review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                latest_reviews[login] = {"reviewer": login, "state": review.get("state"),
                    "commit": review.get("commit_id"), "at": review.get("submitted_at")}
        data = {"number": number, "state": "merged" if detail.get("merged_at") else detail.get("state", "unknown"),
            "author": detail.get("user", {}).get("login"),
            "assignees": [a["login"] for a in detail.get("assignees", []) if a.get("login")],
            "requested_reviewers": [a["login"] for a in detail.get("requested_reviewers", []) if a.get("login")],
            "requested_teams": [a["slug"] for a in detail.get("requested_teams", []) if a.get("slug")],
            "head": detail.get("head", {}).get("sha"), "base": detail.get("base", {}).get("sha"),
            "files": [{k: f.get(k) for k in ("filename", "status", "additions", "deletions", "previous_filename")} | {"patch": (f.get("patch") or "")[:12000]} for f in files],
            "reviews": list(latest_reviews.values()),
            "checks": [{k: c.get(k) for k in ("name", "status", "conclusion", "head_sha", "html_url")} for c in checks],
            "limitations": coverage, "code_ownership": "Not resolved; author, assignee and reviewer are separate source roles."}
        batch.add(provider="github", scope=scope, project_id=project_id, source_id=f"pr:{number}", kind="pull_request",
            title=detail.get("title", f"PR #{number}"), text=detail.get("body") or "", observed_at=detail.get("updated_at"),
            url=detail.get("html_url", ""), data=data)
    for issue in listing(f"{root}/issues?state=all&sort=updated&direction=desc"):
        if issue.get("pull_request") or not isinstance(issue.get("number"), int):
            continue
        labels = [l.get("name", "") if isinstance(l, dict) else str(l) for l in issue.get("labels", [])]
        batch.add(provider="github", scope=scope, project_id=project_id, source_id=f"issue:{issue['number']}", kind="issue",
            title=issue.get("title", "Issue"), text=issue.get("body") or "", observed_at=issue.get("updated_at"), url=issue.get("html_url", ""),
            data={"number": issue["number"], "state": issue.get("state"), "labels": labels,
                "assignees": [a["login"] for a in issue.get("assignees", []) if a.get("login")],
                "bug_bash": any("bug-bash" in l.lower().replace("_", "-").replace(" ", "-") for l in labels),
                "is_bug": any(re.search(r"\b(?:bug|defect)\b", l, re.I) for l in labels)})
    if len(pulls) > 5:
        batch.limitations.append("PR metadata collected; detailed diff/check coverage is limited to the five most recently updated PRs.")
    return batch


def collect_slack(cfg, scope, project_id, *, reader=None):
    from tpllm.integrations.slack_reader import UrllibSlackReader, read_paginated, SlackRateLimitPolicy
    if not re.fullmatch(r"[A-Za-z0-9_-]+", scope):
        raise ValueError("Use a Slack channel ID")
    policy = SlackRateLimitPolicy(max_attempts=1, max_pages=3)
    reader = reader or UrllibSlackReader(token=cfg.api_key, rate_limit_policy=policy)
    history = read_paginated(reader=reader, method="history", channel=scope, policy=policy)
    batch = Collection()
    if history.truncated:
        batch.limitations.append("Channel history truncated at the configured page bound.")
    messages = list(history.items)
    threads = [m for m in messages if m.get("reply_count", 0)]
    for m in threads[:10]:
        try:
            replies = read_paginated(reader=reader, method="replies", channel=scope, ts=m["ts"], policy=policy)
            messages.extend(replies.items)
            if replies.truncated:
                batch.limitations.append(f"Thread {m['ts']} has more replies.")
        except Exception as exc:
            batch.limitations.append(f"Thread unavailable ({type(exc).__name__}).")
    if len(threads) > 10:
        batch.limitations.append("Reply coverage bounded to ten threads per read.")
    for m in {str(m.get("ts")): m for m in messages}.values():
        try:
            stamp = float(m.get("edited", {}).get("ts") or m["ts"])
        except (KeyError, ValueError, TypeError):
            batch.limitations.append("Slack message has no valid source time.")
            continue
        batch.add(provider="slack", scope=scope, project_id=project_id, source_id=m["ts"], kind="conversation",
            title=(m.get("text") or "Message")[:100], text=m.get("text", ""), observed_at=stamp,
            data={"author": m.get("user"), "thread": m.get("thread_ts") or m["ts"], "channel": scope})
    return batch


def collect_calendar(cfg, scope, project_id, *, service=None, as_of=None):
    from tpllm.integrations.calendar_integration import CalendarIntegration
    service = service or CalendarIntegration(cfg)._get_google_service()
    stamp = as_of or datetime.now(timezone.utc)
    batch = Collection()
    cursor = None
    for _ in range(3):
        args = {"calendarId": scope, "timeMin": (stamp - timedelta(days=7)).isoformat(),
            "timeMax": (stamp + timedelta(days=14)).isoformat(), "singleEvents": True, "maxResults": 100, "showDeleted": True}
        if cursor:
            args["pageToken"] = cursor
        response = service.events().list(**args).execute()
        for event in response.get("items", []):
            batch.add(provider="calendar", scope=scope, project_id=project_id, source_id=event.get("id", ""), kind="meeting",
                title=event.get("summary", "Calendar event"), text=event.get("description", ""), observed_at=event.get("updated"), url=event.get("htmlLink", ""),
                data={"start": event.get("start", {}), "end": event.get("end", {}), "status": event.get("status"),
                    "organizer": event.get("organizer", {}).get("email"), "organizer_self": event.get("organizer", {}).get("self", False),
                    "attendees": [a.get("email") for a in event.get("attendees", []) if a.get("email")],
                    "meeting_link": event.get("hangoutLink", "")})
        cursor = response.get("nextPageToken")
        if not cursor:
            break
    if cursor:
        batch.limitations.append("Calendar events truncated at 300 results.")
    return batch


def collect_zoom(cfg, scope, project_id, *, client=None):
    from tpllm.integrations.zoom_integration import ZoomIntegration
    client = client or ZoomIntegration(cfg)._get_client()
    batch = Collection()
    # Scope is an explicit host account. Account-wide summaries are filtered
    # before any note is retained or sent to the intelligence pipeline.
    cursor = ""
    for _ in range(3):
        response = client.get("/meetings/meeting_summaries", params={"page_size": "100", "next_page_token": cursor})
        for summary in response.get("summaries", []):
            if scope not in {str(summary.get("meeting_host_id", "")), summary.get("meeting_host_email", "")}:
                continue
            identifier = str(summary.get("meeting_uuid") or summary.get("meeting_id") or "")
            if not identifier:
                continue
            encoded = quote(identifier, safe="")
            if identifier.startswith("/") or "//" in identifier:
                encoded = quote(encoded, safe="")
            try:
                detail = client.get(f"/meetings/{encoded}/meeting_summary")
                content = detail.get("summary_content") or "\n".join(
                    str(x.get("summary", "")) for x in detail.get("summary_details", []) if isinstance(x, dict))
                if not content:
                    batch.limitations.append(f"Summary {identifier}: content unavailable; no notes inferred.")
                    continue
                batch.add(provider="zoom", scope=scope, project_id=project_id, source_id=identifier, kind="meeting_notes",
                    title=detail.get("meeting_topic", "AI meeting notes"), text=content,
                    observed_at=detail.get("summary_last_modified_time") or detail.get("meeting_end_time"),
                    url=detail.get("summary_doc_url", ""), data={"meeting_id": detail.get("meeting_id"),
                        "meeting_end": detail.get("meeting_end_time"), "next_steps": detail.get("next_steps", [])})
            except Exception as exc:
                batch.limitations.append(f"Summary unavailable ({type(exc).__name__}).")
        cursor = response.get("next_page_token", "")
        if not cursor:
            break
    if cursor:
        batch.limitations.append("Zoom summary list truncated at 300 records.")
    return batch


COLLECTORS = {"github": collect_github, "slack": collect_slack, "calendar": collect_calendar, "zoom": collect_zoom}
