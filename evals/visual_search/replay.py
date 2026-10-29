"""Replay the synthetic visual-search environment through production adapters.

Usage: python evals/visual_search/replay.py --output /tmp/tpllm-visual-eval
Add --stage initial --serve to inspect an isolated dashboard on port 8766.
No provider credentials are loaded. A socket guard forbids network during replay.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import hashlib
from urllib.parse import urlsplit
from unittest.mock import patch

from tpllm.config import TPLLMConfig, IntegrationConfig
from tpllm.models.program import Program, SuccessMetric, Stakeholder, ProgramHealth
from tpllm.product_api import ProductWorkspace
from tpllm.product_authority import Delegation
from tpllm.product_flow import accept_collection, cycle, derive
from tpllm.product_sources import Collection, collect_github, collect_slack, collect_calendar, collect_zoom

ROOT = Path(__file__).parent
SCOPES = {'github':'example/visual-search','slack':'visual-search-launch-demo',
          'calendar':'engineering-demo','zoom':'engineering@example.test'}


def load(name):
    return json.loads((ROOT/name).read_text())


def output(path, value):
    path.write_text(json.dumps(value, indent=2, default=str)+'\n')


def rebase(value, delta):
    """Shift source timestamps as one timeline; preserve stable provider IDs."""
    if isinstance(value,list): return [rebase(v,delta) for v in value]
    if not isinstance(value,dict): return value
    result={}
    for key,v in value.items():
        if key in {'updated_at','submitted_at','merged_at','updated','dateTime','summary_last_modified_time','meeting_end_time'} and v:
            result[key]=(datetime.fromisoformat(v.replace('Z','+00:00'))+delta).isoformat().replace('+00:00','Z')
        elif key in {'ts','thread_ts'} and v:
            result[key]=f'{float(v)+delta.total_seconds():.6f}'
        else: result[key]=rebase(v,delta)
    return result


class GitHubReader:
    def __init__(self,payload,calls): self.payload,self.calls=payload,calls
    def get(self,path):
        self.calls.append({'provider':'github','path':path})
        return deepcopy(self.payload[path])


class SlackReader:
    def __init__(self,payload,calls): self.payload,self.calls=payload,calls
    def get(self,path):
        self.calls.append({'provider':'slack','path':path})
        method=urlsplit(path).path.rsplit('.',1)[-1]
        return deepcopy(self.payload[method])


class CalendarService:
    def __init__(self,payload,calls): self.payload,self.calls=payload,calls
    def events(self): return self
    def list(self,**kwargs):
        self.calls.append({'provider':'calendar','args':kwargs})
        return self
    def execute(self): return deepcopy(self.payload)


class ZoomClient:
    def __init__(self,payload,calls): self.payload,self.calls=payload,calls
    def get(self,path,params=None):
        self.calls.append({'provider':'zoom','path':path})
        # There is deliberately no response for the outside-scope host.
        return deepcopy(self.payload[path])


def collect(payload,calls,as_of):
    cfg=IntegrationConfig()
    batches=[collect_github(cfg,SCOPES['github'],'visual-search',reader=GitHubReader(payload['github'],calls)),
             collect_slack(cfg,SCOPES['slack'],'visual-search',reader=SlackReader(payload['slack'],calls)),
             collect_calendar(cfg,SCOPES['calendar'],'visual-search',service=CalendarService(payload['calendar'],calls),as_of=as_of),
             collect_zoom(cfg,SCOPES['zoom'],'visual-search',client=ZoomClient(payload['zoom'],calls))]
    return Collection(records=[r for b in batches for r in b.records],limitations=[l for b in batches for l in b.limitations])


def collection_receipts(ws,batch,at):
    for provider in SCOPES:
        ws.put('collection:'+provider,{'status':'demo','last_success':at.isoformat(),
            'records_seen':sum(r.provider==provider for r in batch.records),
            'limitations':['Synthetic provider response replay. No external account accessed.']})


def score_cases(scenario,key):
    expected={c['id']:c['expected'] for c in scenario['case_set']}
    results=scenario['simulated_outputs'][key]
    assert len(expected)==100 and len(results)==100
    assert len({r['case_id'] for r in results})==100
    assert {r['case_id'] for r in results}==set(expected)
    recognition=sum(r['recognition']==expected[r['case_id']]['recognition'] for r in results)
    request=sum(all(r[k]==expected[r['case_id']][k] for k in ('decision','category','color')) for r in results)
    return {'recognition_correct':recognition,'request_correct':request,'cases':len(results),
            'recognition_percent':recognition/len(results)*100,'request_match_percent':request/len(results)*100,
            'basis':'Authored symbolic outputs; no vision inference executed.'}


def prepare(ws,scenario,anchor):
    names={'visual-search':'AI visual search','vision-model':'Vision model & inference',
           'data-evals':'Data packages & evaluations','cloud-serving':'Cloud serving & observability'}
    for pid in scenario['project_ids']:
        metrics=[SuccessMetric(name=n,target=f'{v}%',measurement_method='Synthetic provider claim; independently authored symbolic cases.')
                 for n,v in scenario['committed_targets'].items()] if pid=='visual-search' else []
        ws.store.save(pid,'program',Program(id=pid,name=names[pid],goal=scenario['purpose'],health=ProgramHealth.YELLOW,
            success_metrics=metrics,stakeholders=[Stakeholder(name=p['name'],role=p['role'],team='Synthetic visual-search team') for p in scenario['people']]))
        ws.put('setup:'+pid,{'authority':'Local operator','scope':scenario['purpose'],'permission':'Human review',
                             'outlook_confirmed':True,'health_rationale':'Synthetic evaluation environment; release approval remains absent.'})
    # Suppress the separate demonstration seeder; the provider fixtures are the inputs.
    ws.put('seeded',True)
    ws.put('coordination_demo_seeded',True)
    for provider,scope in SCOPES.items():
        ws.put('connection:'+provider,{'scope':scope,'program_id':'visual-search'})
        ws.put('collection:'+provider,{'status':'demo','last_success':anchor.isoformat(),
             'limitations':['Synthetic provider response replay. No external account accessed.']})
    grant=Delegation(project_id='visual-search',destinations=[SCOPES['slack']],
        source_scopes=[p+':'+s for p,s in SCOPES.items()],purpose='Simulate source-backed internal project updates only.',
        expires_at=anchor+timedelta(days=2),minimum_interval_minutes=30,max_per_day=4,granted_by='simulation-only')
    ws.put('delegations',[grant.model_dump(mode='json')])


def run(out,stage):
    scenario,payloads=load('scenario.json'),load('provider_payloads.json')
    assert scenario['synthetic'] and payloads['synthetic']
    if (out/'product-demo'/'tpllm.db').exists():
        raise SystemExit('Choose a fresh --output directory; existing simulation evidence will not be overwritten.')
    out.mkdir(parents=True,exist_ok=True)
    config=TPLLMConfig(data_dir=out)
    ws=ProductWorkspace(config,demo=True)
    now=datetime.now(timezone.utc)
    anchor=now-timedelta(minutes=100)
    delta=anchor-datetime.fromisoformat(payloads['base_time'].replace('Z','+00:00'))
    anchor_naive=anchor.replace(tzinfo=None)
    checks=[];calls=[];stages=[]
    def check(name,condition,detail,known_gap=False):
        checks.append({'name':name,'status':'PASS' if condition else 'GAP' if known_gap else 'FAIL','detail':detail})
    with patch('socket.socket.connect',side_effect=AssertionError('External network is forbidden in simulation')) as network:
        prepare(ws,scenario,anchor_naive)
        for spec in scenario['stages']:
            name=spec['id']
            payload=rebase(payloads['stages'][name],delta)
            at=anchor_naive+timedelta(minutes=spec['minutes'],seconds=5)
            batch=collect(payload,calls,anchor+timedelta(minutes=spec['minutes']))
            check(name+': all provider payloads normalize',not batch.limitations,{'count':len(batch.records),'limitations':batch.limitations})
            accepted=accept_collection(ws,batch,at=at)
            collection_receipts(ws,batch,at)
            cycle(ws,at=at)
            snapshot=ws.snapshot()
            board=next(p['artifacts'].get('task_board',{}) for p in snapshot['programs'] if p['id']=='visual-search')
            pr=snapshot['coordination']['engineering'][0]
            score=score_cases(scenario,'full_eval_complete' if name=='full_eval_complete' else 'initial')
            check(name+': independently computed symbolic totals',score['request_match_percent']==spec['expected_request_match'] and score['recognition_percent']==spec['expected_recognition'],score)
            check(name+': committed targets retained',all(k['committed_target']==f"{scenario['committed_targets'][k['name']]}%" for k in snapshot['coordination']['kpis']),
                  {k['name']:k['committed_target'] for k in snapshot['coordination']['kpis']})
            expected_values={'Request match accuracy':spec['expected_request_match'],'Recognition accuracy':spec['expected_recognition'],'Package alignment':spec['expected_alignment']}
            if name=='initial':
                check('Multiple metrics in one paragraph keep their own values',all(k['trend']['latest_value']==expected_values[k['name']] for k in snapshot['coordination']['kpis']),
                      [{'name':k['name'],'expected':expected_values[k['name']],'shown':k['trend']['latest_value']} for k in snapshot['coordination']['kpis']],known_gap=True)
                check('Proposed target belongs only to the named KPI',all(not any(c['target_candidate'] is not None for c in k['claims']) for k in snapshot['coordination']['kpis'] if k['name']!='Request match accuracy'),
                      {k['name']:[c['target_candidate'] for c in k['claims'] if c['target_candidate'] is not None] for k in snapshot['coordination']['kpis']})
            check(name+': linked technical meeting gets async alternative',snapshot['coordination']['meetings'][0]['assessment']=='inferred',snapshot['coordination']['meetings'][0]['recommendation'])
            check(name+': only simulated delivery',all(r['status']=='simulated' and r['provider_reference']=='demo-only' for r in snapshot['coordination']['receipts']),
                  {'receipts':len(snapshot['coordination']['receipts']),'external_sends':0})
            if name=='regression_fixed':
                check('One regression fix does not establish release readiness',any('not completed' in r for r in pr['estimate_basis']),pr['estimate_basis'])
                check('Closed bug does not close the full evaluation task',any(t['source_id'].endswith('issue:73') and t['status']=='DONE' for t in board.get('tasks',[])) and any(t['source_id'].endswith('issue:74') and t['status']!='DONE' for t in board.get('tasks',[])),
                      [{'title':t['title'],'status':t['status']} for t in board.get('tasks',[]) if t.get('source')=='GITHUB_ISSUE'])
            if name=='full_eval_complete':
                check('Passing checks retain the product and approval boundary',any('approval evidence' in r for r in pr['estimate_basis']),pr['estimate_basis'])
            before=len(ws.evidence());receipts=len(snapshot['coordination']['receipts'])
            replay=accept_collection(ws,batch,at=at)
            cycle(ws,at=at)
            check(name+': repeated read is idempotent',replay['changed']==0 and len(ws.evidence())==before and len(ws.get('action_receipts',[]))==receipts,
                  {'new_evidence':replay['changed'],'receipts':receipts})
            stage_result={'stage':name,'accepted':accepted,'symbolic_evaluation':score,'full_evaluation':spec['full_evaluation'],
                          'release_authorized':False,'source_records':len(snapshot['coordination']['records']),'evidence_records':len(snapshot['evidence'])}
            stages.append(stage_result)
            ws.put('simulation',{'stage':name,'anchor':anchor.isoformat(),'delta_seconds':delta.total_seconds()})
            output(out/(name+'-snapshot.json'),snapshot)
            if stage==name: break
        check('No external network call attempted',network.call_count==0,{'attempts':network.call_count})
        check('Outside Zoom host is excluded',not any(c.get('path')=='/meetings/outside-scope/meeting_summary' for c in calls),{'outside_scope_reads':0})
        # These product requirements are intentionally evaluated without seeded answers.
        check('New target conflict generates a precise human review',bool(ws.get('clarifications',[])),
              {'clarifications':len(ws.get('clarifications',[])),'expected':'Research/reconcile 95% versus proposed 97%; ask only a remaining consequential question.'},known_gap=True)
        original=ws.get('source_records')
        perturbed=deepcopy(original)
        for r in perturbed.values():
            if r['provider']=='slack' and 'Can we use' in r['text']:
                r['text']=r['text'].replace('Can we use the Visual search engineering review for a launch date update?',
                    'When can customers use the feature? Please address this at the Visual search engineering review.')
        ws.put('source_records',perturbed)
        alternate=derive(ws,at=at)
        check('Equivalent wording retains meeting intervention',alternate['meetings'][0]['assessment']=='inferred',
              {'assessment':alternate['meetings'][0]['assessment'],'actions':len(alternate['actions'])},known_gap=True)
        ws.put('source_records',original);derive(ws,at=at)
    import tpllm.product_sources, tpllm.product_flow, tpllm.product_api, tpllm.product_authority
    import tpllm.intelligence.kpi_extraction
    modules=[tpllm.product_sources,tpllm.product_flow,tpllm.product_api,tpllm.product_authority,tpllm.intelligence.kpi_extraction]
    source_hashes={m.__name__:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules}
    report={'synthetic':True,'created_at':now.isoformat(),'stages':stages,'checks':checks,'source_file_sha256':source_hashes,
            'counts':{s:sum(c['status']==s for c in checks) for s in ['PASS','FAIL','GAP']},
            'limitations':scenario['limitations'],'model_calls':0,'external_network_calls':0,'external_messages':0}
    output(out/'report.json',report);output(out/'adapter_calls.json',calls)
    ws.db.connection.close()
    print(json.dumps({'output':str(out),'counts':report['counts'],'stages':[s['stage'] for s in stages]},indent=2))
    return config,report


def simulation_app(config):
    """Use normal product screens with fixture-backed advance on this server only."""
    from fastapi.responses import JSONResponse
    from tpllm.dashboard import create_app
    app=create_app(config,collect_in_background=False)

    @app.middleware('http')
    async def fixture_controls(request,call_next):
        if request.method!='POST' or request.url.path not in {'/api/product/demo/advance','/api/product/demo/restart'}:
            return await call_next(request)
        origin=request.headers.get('origin')
        if request.headers.get('x-tpllm-client')!='dashboard' or origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Use the local simulation dashboard.'},status_code=403)
        if request.url.path.endswith('/restart'):
            return JSONResponse({'detail':'This fixture run preserves its evidence. Start replay.py with a fresh --output directory to reset.'},status_code=409)
        ws=app.state.product_space('demo')
        with ws.lock:
            meta=ws.get('simulation')
            scenario=load('scenario.json')
            index=next(i for i,s in enumerate(scenario['stages']) if s['id']==meta['stage'])
            if index==len(scenario['stages'])-1:
                return JSONResponse({'stage':meta['stage'],'already_applied':True})
            spec=scenario['stages'][index+1]
            delta=timedelta(seconds=meta['delta_seconds'])
            payload=rebase(load('provider_payloads.json')['stages'][spec['id']],delta)
            calls=[]
            anchor=datetime.fromisoformat(meta['anchor'])
            at=anchor.replace(tzinfo=None)+timedelta(minutes=spec['minutes'],seconds=5)
            # Suppress source network and the demo-only sender never contacts Slack.
            with patch('socket.socket.connect',side_effect=AssertionError('External network forbidden in simulation')):
                batch=collect(payload,calls,anchor+timedelta(minutes=spec['minutes']))
                if batch.limitations:
                    return JSONResponse({'detail':batch.limitations},status_code=500)
                result=accept_collection(ws,batch,at=at)
                collection_receipts(ws,batch,at)
                cycle(ws,at=at)
            ws.put('simulation',{**meta,'stage':spec['id']})
            output(config.data_dir/(spec['id']+'-snapshot.json'),ws.snapshot())
            return JSONResponse({'stage':spec['id'],**result})
    return app


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--stage',choices=['all','initial','regression_fixed','full_eval_complete'],default='all')
    parser.add_argument('--strict',action='store_true',help='Also fail for known unmet product expectations.')
    parser.add_argument('--serve',action='store_true',help='Open the isolated local dashboard; choose Explore demo workspace.')
    parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    config,report=run(args.output.expanduser().resolve(),args.stage)
    if args.serve:
        import uvicorn
        print(f'Isolated simulation: http://127.0.0.1:{args.port} — choose Explore demo workspace.',flush=True)
        uvicorn.run(simulation_app(config),host='127.0.0.1',port=args.port)
    raise SystemExit(1 if report['counts']['FAIL'] or args.strict and report['counts']['GAP'] else 0)


if __name__=='__main__': main()
