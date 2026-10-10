#!/usr/bin/env python3
"""Strict synthetic local voice HTTP contracts and negative semantic states."""
import json
import sys
from copy import deepcopy
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
ROOT = Path(__file__).resolve().parent
spec = json.loads((ROOT / 'openapi.yaml').read_text())
paths = {'/v2/voice/sessions': 'post', '/v2/voice/sessions/{session_id}': 'delete',
 '/v2/voice/sessions/{session_id}/turns': 'post',
 '/v2/voice/sessions/{session_id}/turns/{turn_id}/cancel': 'post',
 '/v2/voice/sessions/{session_id}/turns/{turn_id}/result': 'get'}
assert all(path in spec['paths'] and method in spec['paths'][path] for path, method in paths.items()), 'Missing authoritative authenticated local voice HTTP lifecycle contracts'
def validator(name):
 return Draft202012Validator({'$ref': '#/components/schemas/' + name, 'components': spec['components']}, format_checker=FormatChecker())
for name in spec['components']['schemas']:
 if name.startswith('VoiceHTTP'):
  Draft202012Validator.check_schema(spec['components']['schemas'][name])
mapping = {'created': 'VoiceHTTPSessionResponse', 'accepted': 'VoiceHTTPTurnResponse',
 'cancelled': 'VoiceHTTPCancelResponse', 'closed': 'VoiceHTTPCloseResponse',
 'pending': 'VoiceHTTPResultResponse', 'failed': 'VoiceHTTPResultResponse', 'unavailable': 'StateErrorResponse'}
for stem in ('recovery', 'recovery_noop', 'recovery_precision', 'recovery_unsupported'):
 mapping[stem + '_session'] = 'VoiceHTTPSessionRequest'
 mapping[stem + '_turn'] = 'VoiceHTTPTurnRequest'
 mapping[stem + '_result'] = 'VoiceHTTPResultResponse'
mapping['text_proposal_turn'] = 'VoiceHTTPTurnRequest'
mapping['text_proposal_result'] = 'VoiceHTTPResultResponse'
mapping['text_proposal_failed'] = 'VoiceHTTPResultResponse'
values = {}
for name, schema in mapping.items():
 value = json.loads((ROOT / 'examples' / ('voice_http_' + name + '.json')).read_text()); validator(schema).validate(value); values[name] = value
session = values['recovery_session']; turn = values['recovery_turn']; ready = values['recovery_result']
text_turn = values['text_proposal_turn']; text_ready = values['text_proposal_result']
for schema, value, mutate in [
 ('VoiceHTTPSessionRequest', session, lambda q: q.update(principal_id='injected')),
 ('VoiceHTTPSessionRequest', session, lambda q: q['snapshot'].update(synthetic=False)),
 ('VoiceHTTPSessionRequest', session, lambda q: q['snapshot']['inventory'][0].update(starting_stock=2.0)),
 ('VoiceHTTPTurnRequest', turn, lambda q: q['call'].update(name='apply_plan')),
 ('VoiceHTTPTurnRequest', turn, lambda q: q['call']['arguments'].update(workspace_id='foreign')),
 ('VoiceHTTPTurnRequest', turn, lambda q: q['call']['arguments'].pop('assignments')),
 ('VoiceHTTPTurnRequest', text_turn, lambda q: q.update(call=deepcopy(turn['call']))),
 ('VoiceHTTPTurnRequest', text_turn, lambda q: q.pop('input_mode')),
 ('VoiceHTTPTurnRequest', text_turn, lambda q: q.pop('utterance')),
 ('VoiceHTTPTurnRequest', text_turn, lambda q: q.update(utterance='')),
 ('VoiceHTTPTurnRequest', text_turn, lambda q: q.update(utterance='a' * 4097)),
 ('VoiceHTTPTurnRequest', text_turn, lambda q: q.update(snapshot=deepcopy(session['snapshot']))),
 ('VoiceHTTPResultResponse', ready, lambda q: q.update(state='pending')),
 ('VoiceHTTPResultResponse', ready, lambda q: q.update(error={'code':'execution_unavailable','message':'Unavailable'})),
 ('VoiceHTTPResultResponse', values['pending'], lambda q: q.update(outcome=ready['outcome'])),
 ('VoiceHTTPResultResponse', values['failed'], lambda q: q.pop('error')),
 ('VoiceHTTPResultResponse', ready, lambda q: q['outcome'].update(read_only=False)),
 ('VoiceHTTPResultResponse', ready, lambda q: q['outcome']['result']['recovery']['orders'][0].update(late_by_seconds=3600)),
 ('VoiceHTTPResultResponse', text_ready, lambda q: q['outcome'].update(interpretation_verified=True)),
 ('VoiceHTTPResultResponse', text_ready, lambda q: q['outcome'].update(result=deepcopy(ready['outcome']['result']))),
 ('VoiceHTTPResultResponse', text_ready, lambda q: q['outcome']['ownership'].update(turn_id='0')),
 ('VoiceHTTPResultResponse', text_ready, lambda q: q['outcome']['call'].update(name='apply_plan')),
 ('VoiceHTTPResultResponse', values['text_proposal_failed'], lambda q: q['error'].update(message='Source details leaked')),
 ('VoiceHTTPResultResponse', values['text_proposal_failed'], lambda q: q['error'].update(code='source_error')),
]:
 q=deepcopy(value); mutate(q); assert list(validator(schema).iter_errors(q)), 'Invalid local voice HTTP semantic state accepted: ' + schema
precision=json.loads((ROOT/'examples/recovery_precision_request.json').read_text())
assert values['recovery_precision_session']['snapshot']==precision['snapshot'], 'Precision snapshot changed'
assert values['recovery_precision_turn']['call']['arguments']['assignments']==precision['recovery']['assignments'], 'Precision assignments changed'
assert text_ready['outcome']['call']==turn['call'], 'Text proposal call differs from the visible typed confirmation witness'
# Schema-only mode is isolated before every product import and numerical comparison.
# The historical no-argument path below is unchanged and still checks its results.
schema_only = sys.argv[1:] == ['--schema-only']
if sys.argv[1:] and not schema_only:
 raise SystemExit('Expected --schema-only or no arguments')
if schema_only:
 print(str(len(mapping)) + ' local synthetic voice HTTP schema fixtures valid; numerical comparison not run')
 raise SystemExit(0)
# New contract fixtures also preserve the authoritative numerical result after server scenario identity substitution.
sys.path.insert(0, str(ROOT.parent / 'procurement-engine/src'))
from procure.production.recovery import compare_receipt_recovery
for stem in ('recovery', 'recovery_noop', 'recovery_precision', 'recovery_unsupported'):
 original=json.loads((ROOT/'examples'/(stem+'_request.json')).read_text())
 scenario=deepcopy(original['scenario']); alternative=deepcopy(original['recovery'])
 scenario['scenario_id']=values['created']['scenario_id']; alternative['scenario_id']=scenario['scenario_id']
 actual=compare_receipt_recovery(original['snapshot'], scenario, alternative)
 assert values[stem+'_result']['outcome']['result']==actual, 'Synthetic HTTP fixture differs from public comparison: '+stem
assert values['recovery_result']['outcome']['result']['disrupted_baseline']['orders'][0]['demands'][0]['shortfall']=='4'
assert values['recovery_result']['outcome']['result']['recovery']['orders'][0]['late_by_seconds']=='3600'
print(str(len(mapping)) + ' local synthetic voice HTTP fixtures valid; identity/function/float/unknown/response-state mutations rejected')
