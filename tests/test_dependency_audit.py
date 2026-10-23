"""An unavailable vulnerability check is never evidence of a clean closure."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "dependency_audit", Path(__file__).parents[1] / "scripts/audit_dependencies.py"
)
audit_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_script)


def test_missing_audit_report_is_an_error(monkeypatch):
    monkeypatch.setattr(audit_script.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=2))
    with pytest.raises(RuntimeError, match="valid report"):
        audit_script.audit(["example==1.0"])


def test_skipped_package_is_an_error(monkeypatch):
    def fake_run(args, **kwargs):
        Path(args[-1]).write_text(json.dumps({"dependencies": [{"name": "example", "skip_reason": "unavailable"}]}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(audit_script.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="skipped"):
        audit_script.audit(["example==1.0"])


def test_unresolved_base_fails_the_release_check(monkeypatch, capsys):
    def fail(*args):
        raise RuntimeError("resolution unavailable")
    monkeypatch.setattr(audit_script, "resolve_closure", fail)
    monkeypatch.setattr(audit_script.sys, "argv", ["audit", "--extras", "--json"])
    assert audit_script.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["blocking"] is True
    assert "error" in report["closures"]["base"]
