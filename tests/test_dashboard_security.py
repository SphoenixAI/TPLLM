"""Exercise the actual packaged renderer against hostile source text.

The new frontend uses a packaged external script, so inline scripts are denied
by CSP. Escaping still protects every dynamic template; tests run the real page
functions in Node rather than copying an approximate row renderer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import shutil
import subprocess

import pytest

from tpllm import dashboard

WEB = Path(dashboard.__file__).parent / "web"
PAYLOADS = ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>',
            '\" onmouseover=\"alert(1)', "'><svg/onload=alert(1)>",
            '<iframe src=javascript:alert(1)>', '&lt;already escaped&gt;']


def run_js(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    # Stop before bootstrapping listeners; all renderer functions are real code.
    source = (WEB / "app.js").read_text().split("// Browser bootstrap", 1)[0]
    prelude = "const localStorage={getItem:()=>null}; const document={querySelector:()=>null};"
    result = subprocess.run([node, "-e", prelude + (WEB / "flow.js").read_text() + source + "\n" + body], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_escaping_and_null_handling():
    results = run_js(f"console.log(JSON.stringify({json.dumps(PAYLOADS)}.map(esc)));")
    assert all("<" not in r and ">" not in r and '"' not in r and "'" not in r for r in results)
    assert run_js("console.log(JSON.stringify([esc(null),esc(undefined),esc(0),esc('&lt;b&gt;')]));") == ["", "", "0", "&amp;lt;b&amp;gt;"]


def test_actual_task_project_decision_and_source_renderers_escape_payloads():
    body = r'''
const payload='<img src=x onerror=alert(1)>';
const task={id:payload,title:payload,assignee:payload,priority:payload,status:payload,due_date:payload};
const decision={id:payload,title:payload,context:payload,status:'PROPOSED'};
const program={id:payload,name:payload,goal:payload,health:'GREEN',outlook:'Unknown',non_goals:[],success_metrics:[],setup:{},evidence_count:1,decision_reviews:{},artifacts:{task_board:{tasks:[task]},decision_log:{decisions:[decision]}}};
const evidence={evidence_id:payload,source_id:payload,source_system:payload,title:payload,normalized_content:payload,project_ids:[payload],source_pointer:'javascript:alert(1)'};
S.data={programs:[program],evidence:[evidence],clarifications:[],activity:[],connections:[],revision:null,settings:{},coverage:{engine_backed:[],derived:[]}};
console.log(JSON.stringify([renderTasks(),renderProjects(),renderDecisions(),evidenceCards([evidence]),renderDashboard()]));
'''
    outputs = run_js(body)
    for output in outputs:
        assert "<img" not in output
        assert "&lt;img" in output
        assert 'href="javascript:' not in output
        assert "<script" not in output


def test_only_http_and_https_source_links_are_clickable():
    assert run_js("console.log(JSON.stringify(['javascript:alert(1)','data:text/html,hi','file:///tmp/x','https://example.com/a','http://localhost'].map(safeSourceUrl)));") == [False, False, False, True, True]


def test_data_does_not_become_inline_handlers():
    source = (WEB / "app.js").read_text()
    assert 'onclick="' not in source
    assert 'onerror="' not in source
    assert "eval(" not in source
    assert "new Function" not in source


@pytest.fixture
def client(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("jsonschema")
    from fastapi.testclient import TestClient
    from tpllm.config import TPLLMConfig
    return TestClient(dashboard.create_app(TPLLMConfig(data_dir=tmp_path)))


def test_csp_disallows_inline_scripts_eval_remote_sources_and_framing(client):
    for path in ('/', '/health', '/assets/app.js', '/api/product/state'):
        response = client.get(path)
        assert response.status_code == 200
        csp = response.headers['content-security-policy']
        assert "script-src 'self';" in csp
        assert "unsafe-eval" not in csp
        assert "connect-src 'self'" in csp
        assert "object-src 'none'" in csp
        assert "base-uri 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['x-frame-options'] == 'DENY'
        assert 'strict-transport-security' not in response.headers


def test_packaged_assets_are_served_and_html_is_static(client):
    html = client.get('/').text
    assert '<script src="/assets/app.js" defer>' in html
    assert '<script>' not in html
    assert 'function esc(value)' in client.get('/assets/app.js').text
    assert re.search(r"@media\s*\(max-width:\s*700px\)", client.get('/assets/app.css').text)
    assert client.get('/assets/../config.py').status_code == 404
    assert client.get('/assets/%2e%2e/config.py').status_code == 404


def test_server_json_retains_original_text_for_non_browser_consumers(client):
    headers={'X-TPLLM-Client':'dashboard'}
    payload=PAYLOADS[1]
    response=client.post('/api/product/actions/create_program?workspace=demo',headers=headers,json={'arguments':{'program_id':'hostile','name':payload,'goal':payload}})
    assert response.status_code == 200
    assert client.get('/api/product/state?workspace=demo').json()['programs'][0]['name'] == payload
