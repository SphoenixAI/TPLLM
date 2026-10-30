"""Evaluate private, live connector readbacks without contacting providers again.

Inputs are captures from connected Calendar, Gmail and Zoom tools, not mocked
provider responses. The capture bridge is explicit: it is not a product collector.
Never commit the account-specific captures, join links or output database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from tpllm.config import TPLLMConfig, IntegrationConfig
from tpllm.models.program import Program, SuccessMetric, Stakeholder, ProgramHealth
from tpllm.product_api import ProductWorkspace
from tpllm.product_flow import accept_collection, cycle, recover_sources
from tpllm.product_sources import Collection, COLLECTORS, collect_calendar, utc


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if tag == 'a' and key == 'href':
                self.parts.append(value)
        if tag in {'p', 'br', 'h2'}:
            self.parts.append('\n')


def plain(value):
    parser = PlainText()
    parser.feed(value)
    return ' '.join(parser.parts)


class CalendarCapture:
    def __init__(self, rows):
        self.rows = rows

    def events(self):
        return self

    def list(self, **kwargs):
        return self

    def execute(self):
        # Preserve absent updated timestamps. Do not invent source modification.
        return {'items': [{**row, 'htmlLink': row['url'],
                           'start': {'dateTime': row['start']},
                           'end': {'dateTime': row['end']},
                           'organizer': {'email': row['organizer']['email'], 'self': row['organizer']['is_self']}}
                          for row in self.rows]}


def run(capture_path, manifest_path, out):
    if out.exists():
        raise SystemExit('Choose a fresh --output directory.')
    capture = json.loads(capture_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    scenario = json.loads(Path(__file__).with_name('scenario.json').read_text())
    authored = json.loads(Path(__file__).with_name('engagement_workflow.json').read_text())
    import tpllm.product_flow, tpllm.product_sources, tpllm.intelligence.kpi_extraction
    source_hashes = {m.__name__: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (tpllm.product_flow, tpllm.product_sources, tpllm.intelligence.kpi_extraction)}
    assert capture['run'] == manifest['run'] == authored['run'] == 'VSIM-20261030'
    captured = utc(capture['captured_at'])
    at = max(datetime.now(timezone.utc).replace(tzinfo=None), captured)
    events = capture['calendar']['responses']
    drafts = capture['gmail']['responses']
    docs = [capture['document_' + m['key']] for m in manifest['documents']]
    checks = []

    def check(name, condition, detail=None, gap=False):
        checks.append({'name': name, 'status': 'PASS' if condition else 'GAP' if gap else 'FAIL', 'detail': detail})

    check('Four distinct Zoom creation acknowledgements', len({x['id'] for x in manifest['meetings']}) == 4)
    check('Requested Zoom times and no guests', all(x['start_time'] == f'2026-10-30T{16+i}:00:00Z' and x['duration'] == 30 and x['settings']['meeting_invitees'] == [] for i, x in enumerate(manifest['meetings'])))
    check('Calendar live readback contains four events', len(events) == 4)
    check('Calendar times match user schedule', all(e['start'] == f'2026-10-30T{9+i:02d}:00:00-07:00' and e['end'] == f'2026-10-30T{9+i:02d}:30:00-07:00' for i, e in enumerate(events)))
    check('Calendar is private, free, guestless and silent', all(e['attendees'] == [] and e['visibility'] == 'private' and e['transparency'] == 'transparent' and e['reminders'] == {'use_default': False, 'overrides': []} for e in events))
    check('Calendar events point to matching Zoom meetings', all(e['location'] == z['join_url'] for e, z in zip(events, manifest['meetings'])))
    check('Sixteen unsent drafts read back', len(drafts) == 16 and all('DRAFT' in d['label_ids'] and 'SENT' not in d['label_ids'] for d in drafts))
    check('No draft has recipient headers', all(not h['value'] for d in drafts for h in d['payload']['headers'] if h['name'].lower() in {'to', 'cc', 'bcc'}))
    check('All authored email bodies survived provider round trip', all(e['body'] in plain(d['payload']['body']['content']) for e, d in zip(authored['emails'], drafts)))
    check('Four Zoom documents retain fictional provenance', len(docs) == 4 and all('AUTHORED ROLE-PLAY SCRIPT' in d['file_content'] and 'not a transcript' in d['file_content'] for d in docs))
    check('All scripted meeting utterances survived round trip', all(all(line in d['file_content'] for line in s['script']) for s, d in zip(authored['meeting_scripts'], docs)))
    expected_docs = {d['result']['file_id'] for d in manifest['documents']}
    found_docs = {d['file_id'] for d in capture['zoom_search']['data'] if d.get('public_status') == 'private'}
    check('Zoom documents are private in search readback', expected_docs <= found_docs)

    batch = Collection(limitations=[
        'Manual connector-capture bridge; not unattended TPLLM collection.',
        'Calendar connector omits provider updated timestamp; observed_at is capture time.',
        'Gmail entries are unsent drafts; Zoom documents are authored scripts, not completed meeting notes.',
        'Fictional timestamps stay in content/data; they are not source modification or delivery times.',
    ])
    for e in events:
        batch.add(provider='calendar', scope='primary', project_id='visual-search', source_id=e['id'], kind='meeting', title=e['summary'], text=e['description'], observed_at=captured, url=e['url'], data={'synthetic': True, 'observation_basis': 'connector_read_time', 'provider_updated_at': None, 'start': {'dateTime': e['start']}, 'end': {'dateTime': e['end']}, 'status': e['status'], 'attendees': [], 'organizer_self': True, 'meeting_link': e['location']})
    for d, m in zip(drafts, authored['emails']):
        headers = {h['name'].lower(): h['value'] for h in d['payload']['headers']}
        batch.add(provider='gmail', scope=authored['run'], project_id='visual-search', source_id=d['id'], kind='email_draft', title=headers['subject'], text=plain(d['payload']['body']['content']), observed_at=captured, url='https://mail.google.com/mail/u/0/#drafts/' + d['id'], data={'synthetic': True, 'delivery_state': 'unsent_draft', 'labels': d['label_ids'], 'provider_internal_date': d['internal_date'], 'observation_basis': 'connector_read_time', 'scenario_time': m['scenario_time'], 'fictional_role': m['fictional_role'], 'engagement_id': m['id'], 'reply_to_scenario_id': m['reply_to']})
    for d, m in zip(docs, manifest['documents']):
        batch.add(provider='zoom', scope=authored['run'], project_id='visual-search', source_id=m['result']['file_id'], kind='authored_script', title=d['file_name'], text=d['file_content'], observed_at=captured, url=m['result']['file_link'], data={'synthetic': True, 'meeting_occurred': False, 'observation_basis': 'connector_read_time'})

    out.mkdir(parents=True)
    with patch('socket.socket.connect', side_effect=AssertionError('No network permitted during capture evaluation')) as network:
        direct = collect_calendar(IntegrationConfig(), 'primary', 'visual-search', service=CalendarCapture(events))
        check('Calendar connector data meets production timestamp contract', len(direct.records) == 4, direct.limitations, gap=True)
        check('Product has automatic Gmail draft intake', 'gmail' in COLLECTORS, 'Current collectors: ' + ', '.join(COLLECTORS), gap=True)
        # The registered Zoom collector reads meeting summaries, not Hub documents.
        check('Product automatically collects Zoom Hub scripts', False, 'Zoom collector reads meeting summaries; Hub docs require the explicit capture bridge.', gap=True)
        ws = ProductWorkspace(TPLLMConfig(data_dir=out), demo=True)
        program = Program(id='visual-search', name='AI visual search: connected simulation', goal=scenario['purpose'], health=ProgramHealth.YELLOW, success_metrics=[SuccessMetric(name=n, target=f'{v}%', measurement_method='Synthetic communication claims, not independently measured performance') for n, v in scenario['committed_targets'].items()], stakeholders=[Stakeholder(name=p['name'], role=p['role'], team='Fictional team') for p in scenario['people']])
        ws.store.save('visual-search', 'program', program)
        ws.put('seeded', True)
        ws.put('coordination_demo_seeded', True)
        ws.put('delegations', [])
        ws.put('collection_settings', {})
        accepted = accept_collection(ws, batch, at=at)
        cycle(ws, at=at)
        snapshot = ws.snapshot()
        check('All 24 bridged source records journaled', accepted['changed'] == 24, accepted)
        check('Scenario provenance retained', all(r['data'].get('synthetic') for r in snapshot['coordination']['records']))
        check('Authored scripts are not reclassified as meeting transcripts', not any(r['kind'] == 'meeting_notes' for r in snapshot['coordination']['records']))
        check('Explicit source links produce relationships', len(snapshot['coordination']['relationships']) >= 24, len(snapshot['coordination']['relationships']))
        check('All scheduled meetings appear in coordination', len(snapshot['coordination']['meetings']) == 4)
        check('Draft content cannot change approved KPI targets', all(k['committed_target'] == f"{scenario['committed_targets'][k['name']]}%" for k in snapshot['coordination']['kpis']))
        check('Drafts and authored scenarios do not become observed KPI values', all(k['trend']['latest_value'] is None and k['trend']['observation_count'] == 0 for k in snapshot['coordination']['kpis']), {k['name']: k['trend']['latest_value'] for k in snapshot['coordination']['kpis']}, gap=True)
        check('Request target proposals do not contaminate other KPIs', not any(c.get('target_candidate') in {95, 97} for k in snapshot['coordination']['kpis'] if k['name'] != 'Request match accuracy' for c in k['claims']), {k['name']: [c.get('target_candidate') for c in k['claims'] if c.get('target_candidate') is not None] for k in snapshot['coordination']['kpis']}, gap=True)
        check('Role-play authority does not grant execution', snapshot['coordination']['delegations'] == [] and snapshot['coordination']['receipts'] == [] and not any(a['status'] == 'ready' for a in snapshot['coordination']['actions']))
        repeated = accept_collection(ws, batch, at=at)
        check('Repeated capture ingestion is idempotent', repeated['changed'] == 0, repeated)
        recovered = recover_sources(ws)
        check('Journal restores all source records', len(recovered) == 24)
        check('Engine generates the scripted targeted async intervention', any(m['assessment'] == 'inferred' and m.get('draft') for m in snapshot['coordination']['meetings']), 'Drafts remain email_draft, not delivered conversations; no automated Gmail engagement loop exists.', gap=True)
        check('Evaluation performs zero network calls', network.call_count == 0)
        (out/'snapshot.json').write_text(json.dumps(snapshot, indent=2, default=str)+'\n')
        ws.db.connection.close()
    report = {'run': authored['run'], 'capture_sha256': hashlib.sha256(capture_path.read_bytes()).hexdigest(), 'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(), 'evidence_boundary': batch.limitations, 'checks': checks, 'totals': {s: sum(c['status'] == s for c in checks) for s in ('PASS', 'FAIL', 'GAP')}, 'observed_engine_output': {'meetings': [{k: m.get(k) for k in ('title', 'assessment', 'recommendation', 'draft')} for m in snapshot['coordination']['meetings']], 'kpis': [{k: x.get(k) for k in ('name', 'committed_target', 'trend', 'reconciliation')} for x in snapshot['coordination']['kpis']], 'actions': snapshot['coordination']['actions']}}
    report['source_file_sha256'] = source_hashes
    (out/'report.json').write_text(json.dumps(report, indent=2, default=str)+'\n')
    print(json.dumps(report['totals']))
    return 1 if report['totals']['FAIL'] else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.capture, args.manifest, args.output))
