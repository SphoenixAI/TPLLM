"""Build explicitly synthetic provider payloads for the visual-search program.

No network, account, image model, or real person is involved. Image descriptions
and model outputs are symbolic test inputs, never measured CV benchmark results.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

ROOT = Path(__file__).parent
BASE = datetime(2026, 10, 29, 18, tzinfo=timezone.utc)
SCOPE = 'example/visual-search'
PR = f'https://github.com/{SCOPE}/pull/42'
ISSUE = f'https://github.com/{SCOPE}/issues/73'
MEETING = 'https://calendar.example.test/events/visual-search-engineering-review'


def iso(minutes=0):
    return (BASE + timedelta(minutes=minutes)).isoformat().replace('+00:00', 'Z')


def build():
    cases = []
    for i in range(100):
        category = ['backpack', 'backpack', 'handbag', 'backpack', 'unknown'][i // 20]
        color = 'blue' if 20 <= i < 40 else 'red'
        decision = 'abstain' if category == 'unknown' else 'match'
        cases.append({
            'id': f'heldout-{i+1:03}', 'split': 'held_out',
            'image_spec': {'representation': 'symbolic_description_only', 'object': category, 'color': color,
                           'lighting': 'poor' if i % 3 == 0 else 'normal', 'background': 'cluttered' if i % 2 else 'plain'},
            'request': f'Find a {color} {category}, not a '+('backpack' if category == 'handbag' else 'handbag')
                       if category != 'unknown' else 'Find a red backpack; ask if the object is unclear.',
            'expected': {'recognition': category, 'decision': decision, 'category': None if decision=='abstain' else category,
                         'color': None if decision=='abstain' else color},
        })
    def outcomes(recognition_errors, request_errors):
        rows = []
        for i,c in enumerate(cases):
            expected = c['expected']
            rows.append({'case_id': c['id'], 'recognition': 'handbag' if i<recognition_errors else expected['recognition'],
                         'decision': expected['decision'], 'category': 'handbag' if i<request_errors else expected['category'],
                         'color': expected['color']})
        return rows
    initial_results = outcomes(4,11)
    final_results = outcomes(1,4)
    scenario = {
        'schema_version': 1, 'synthetic': True, 'name': 'AI visual search release coordination',
        'purpose': "Test TPLLM on a cloud website feature: 'Find a red backpack, not a handbag'.",
        'limitations': ['No images are provided or inferred over. Cases are symbolic inputs.',
                       'Accuracy values are calculated from authored synthetic outputs, not measured model performance.',
                       'The adapter runtime remains deterministic; these fixtures do not prove general semantic reasoning.',
                       'All external delivery is simulated. No Slack workspace or provider credentials required.'],
        'base_time': iso(), 'project_ids': ['visual-search','vision-model','data-evals','cloud-serving'],
        'people': [{'name':n,'role':r,'id':i} for n,r,i in [
            ('Alex Chen','Computer vision engineer','U_ALEX'),('Riley Shah','Data package owner','U_RILEY'),
            ('Jamie Park','Evaluation lead','U_JAMIE'),('Sam Rivera','Cloud engineer','U_SAM'),
            ('Local operator','Release decision owner','U_RELEASE')]],
        'committed_targets': {'Request match accuracy':95,'Recognition accuracy':98,'Package alignment':100},
        'proposed_unapproved_target': {'Request match accuracy':97},
        'release_bundle': {'model':'vision-0.8.2','catalog_data':'catalog-data-3.2','label_schema':'3.2','eval_suite':'request-evals-2.1'},
        'stages':[
            {'id':'initial','minutes':0,'expected_request_match':89,'expected_recognition':96,'expected_alignment':75,'full_evaluation':'failed','release_authorized':False},
            {'id':'regression_fixed','minutes':45,'expected_request_match':89,'expected_recognition':96,'expected_alignment':75,'full_evaluation':'queued','release_authorized':False},
            {'id':'full_eval_complete','minutes':90,'expected_request_match':96,'expected_recognition':99,'expected_alignment':100,'full_evaluation':'passed','release_authorized':False}],
        'expected_product_limits': ['Paraphrasing the launch-date request can miss the meeting intervention.',
                                    'A new KPI target conflict does not currently create a human-review question.'],
        'case_set': cases,
        'simulated_outputs': {'initial':initial_results,'full_eval_complete':final_results},
        'regression_case': {'id':'backpack-not-handbag-regression','split':'regression',
                            'request':'Find a red backpack, not a handbag','expected':'red backpack',
                            'initial':'red handbag','regression_fixed':'red backpack'},
    }
    payloads = {'synthetic':True,'base_time':iso(),'stages':{}}
    for stage in scenario['stages']:
        name, minute = stage['id'], stage['minutes']
        initial = name=='initial'; complete = name=='full_eval_complete'
        head = ('a' if initial else 'b')*40
        updated = iso(minute)
        checks = [
            {'name':'release-bundle-contract','status':'completed','conclusion':'failure' if initial else 'success','head_sha':head},
            {'name':'request-exclusion-regression','status':'completed','conclusion':'failure' if initial else 'success','head_sha':head},
            {'name':'full-held-out-request-evals','status':'completed' if initial or complete else 'queued',
             'conclusion':'failure' if initial else 'success' if complete else None,'head_sha':head},
            {'name':'cloud-canary-and-rollback','status':'completed' if complete else 'queued','conclusion':'success' if complete else None,'head_sha':head},
            # Old success must not override the current failing/queued check.
            {'name':'old-revision-success','status':'completed','conclusion':'success','head_sha':'0'*40}]
        pr = {'number':42,'title':'Align vision inference with data and request-eval packages',
              'body':f'Pin vision-0.8.2, catalog-data-3.2, labels 3.2 and request-evals-2.1. Fix {ISSUE}. '
                     + ('Cloud serves data 3.1. Exclusion regression fails.' if initial else
                        'One regression is fixed. The full held-out run and cloud canary remain pending.' if not complete else
                        'The full synthetic held-out evaluation and canary passed on this bundle; human release approval is still absent.'),
              'state':'open' if initial else 'closed','merged_at':None if initial else updated,'updated_at':updated,'html_url':PR,
              'user':{'login':'alex'},'assignees':[{'login':'alex'}],'requested_reviewers':[{'login':'jamie'}] if initial else [],
              'requested_teams':[{'slug':'vision-and-evals'}], 'head':{'sha':head},'base':{'sha':'c'*40}}
        bug = {'number':73,'title':'Bug bash: handbag returned for backpack-only request',
               'body':f"'Find a red backpack, not a handbag' returned a handbag. Implementation: {PR}. "+
                      ('Open on staging data3.1.' if initial else 'Single regression verified fixed; this closure does not establish whole-suite accuracy.'),
               'state':'open' if initial else 'closed','updated_at':updated,'html_url':ISSUE,
               'labels':[{'name':'bug'},{'name':'bug-bash'},{'name':'request-fidelity'}],'assignees':[{'login':'alex'}]}
        eval_issue={'number':74,'title':'Run held-out request evals on the exact cloud release bundle','body':f'Depends on {PR}. Jamie owns evaluation; Local operator retains release authority.',
                    'state':'closed' if complete else 'open','updated_at':updated,'html_url':f'https://github.com/{SCOPE}/issues/74',
                    'labels':[{'name':'evaluation'}],'assignees':[{'login':'jamie'}]}
        files=[{'filename':'web/api/visual_search.py','status':'modified','additions':8,'deletions':2,
                'patch':"@@ synthetic request-filter fixture @@\n+ enforce_object_type_and_exclusions(request, candidates)\n+ abstain_if_unknown(image)"},
               {'filename':'packages/release-bundle.json','status':'modified','additions':4,'deletions':1,
                'patch':'- "catalog_data": "3.1"\n+ "catalog_data": "3.2"\n+ "request_evals": "2.1"'}]
        ghroot=f'repos/{SCOPE}'
        github={f'{ghroot}/pulls?state=all&sort=updated&direction=desc&per_page=100&page=1':[pr],
                f'{ghroot}/pulls/42':pr,f'{ghroot}/pulls/42/files?per_page=100&page=1':files,
                f'{ghroot}/pulls/42/reviews?per_page=100&page=1':[
                    {'user':{'login':'reviewer-old'},'state':'APPROVED','commit_id':'0'*40,'submitted_at':iso(-60)},
                    {'user':{'login':'jamie'},'state':'CHANGES_REQUESTED' if initial else 'APPROVED','commit_id':head,'submitted_at':updated}],
                f'{ghroot}/commits/{head}/check-runs?per_page=100&page=1':{'check_runs':checks},
                f'{ghroot}/issues?state=all&sort=updated&direction=desc&per_page=100&page=1':[bug,eval_issue]}
        parent_ts=str(BASE.timestamp())
        slack_parent={'ts':parent_ts,'text':f'Can we use the Visual search engineering review for a launch date update? {MEETING} Related PR {PR}.',
                      'user':'U_PROGRAM','reply_count':2}
        slack_reply={'ts':str((BASE+timedelta(minutes=minute,seconds=1)).timestamp()),'thread_ts':parent_ts,'user':'U_JAMIE',
                     'text':f'Request match accuracy is {stage["expected_request_match"]}%. Recognition accuracy is {stage["expected_recognition"]}%. Package alignment is {stage["expected_alignment"]}%. '
                            'The approved request gate remains 95%; 97% is proposed, not approved. '+
                            ('Full held-out evaluation still pending after the single-case fix.' if name=='regression_fixed' else
                             'Full synthetic evaluation complete; no human release approval recorded.' if complete else
                             'Staging data v3.1 disagrees with evaluated data v3.2. No release approval.')}
        reply_authority={'ts':str((BASE+timedelta(seconds=2)).timestamp()),'thread_ts':parent_ts,'user':'U_RELEASE',
                         'text':'I own the release decision. Keep the committed request-match gate at 95% unless I approve a change. Engineering review is for package and request-fidelity issues; status belongs in this thread.'}
        event={'id':'design-review','summary':'Visual search engineering review','description':f'Resolve preprocessing, label-schema parity and evaluation cases. {PR}. Launch status belongs in a separate asynchronous coordination thread.',
               'updated':iso(),'htmlLink':MEETING,'status':'confirmed','start':{'dateTime':iso(240)},'end':{'dateTime':iso(270)},
               'organizer':{'email':'engineering@example.test','self':False},
               'attendees':[{'email':p+'@example.test'} for p in ['alex','riley','jamie','sam']]}
        notes=f'Request match accuracy is {stage["expected_request_match"]}%. Recognition accuracy is {stage["expected_recognition"]}%. Package alignment is {stage["expected_alignment"]}%. Request match accuracy target is 97%, proposed by the evaluation team; the approved brief still says 95%. '+slack_reply['text']+'\nAlex will fix the backpack exclusion case. Riley will freeze the catalog data and label schema. Jamie will run the full held-out request evaluation. Sam will align the cloud inference container. No beta approval was recorded.'
        zoom={'/meetings/meeting_summaries':{'summaries':[{'meeting_host_email':'engineering@example.test','meeting_uuid':'review-notes-auto'},
                                                               {'meeting_host_email':'outside@example.test','meeting_uuid':'outside-scope'}]},
              '/meetings/review-notes-auto/meeting_summary':{'meeting_id':900001,'meeting_topic':'AI visual search readiness · synthetic AI summary',
                  'summary_content':notes,'summary_last_modified_time':updated,'meeting_end_time':updated,
                  'summary_doc_url':'https://zoom.example.test/summaries/visual-search','next_steps':[]}}
        payloads['stages'][name]={'github':github,'slack':{'history':{'ok':True,'messages':[slack_parent]},
                                                       'replies':{'ok':True,'messages':[slack_parent,reply_authority,slack_reply]}},
                                 'calendar':{'items':[event]},'zoom':zoom}
    for filename,value in [('scenario.json',scenario),('provider_payloads.json',payloads)]:
        (ROOT/filename).write_text(json.dumps(value,indent=2)+'\n')
    print('Created synthetic scenario and provider payloads; no external services accessed.')


if __name__=='__main__':
    build()
