#!/usr/bin/env python3
"""Strict synthetic local voice HTTP contracts and negative semantic states."""
import json
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
values = {}
for name, schema in mapping.items():
 value = json.loads((ROOT / 'examples' / ('voice_http_' + name + '.json')).read_text()); validator(schema).validate(value); values[name] = value
session = values['recovery_session']; turn = values['recovery_turn']; ready = values['recovery_result']
for schema, value, mutate in [
 ('VoiceHTTPSessionRequest', session, lambda q: q.update(principal_id='injected')),
 ('VoiceHTTPSessionRequest', session, lambda q: q['snapshot'].update(synthetic=False)),
 ('VoiceHTTPSessionRequest', session, lambda q: q['snapshot']['inventory'][0].update(starting_stock=2.0)),
 ('VoiceHTTPTurnRequest', turn, lambda q: q['call'].update(name='apply_plan')),
 ('VoiceHTTPTurnRequest', turn, lambda q: q['call']['arguments'].update(workspace_id='foreign')),
 ('VoiceHTTPTurnRequest', turn, lambda q: q['call']['arguments'].pop('assignments')),
 ('VoiceHTTPResultResponse', ready, lambda q: q.update(state='pending')),
 ('VoiceHTTPResultResponse', ready, lambda q: q.update(error={'code':'execution_unavailable','message':'Unavailable'})),
 ('VoiceHTTPResultResponse', values['pending'], lambda q: q.update(outcome=ready['outcome'])),
 ('VoiceHTTPResultResponse', values['failed'], lambda q: q.pop('error')),
 ('VoiceHTTPResultResponse', ready, lambda q: q['outcome'].update(read_only=False)),
 ('VoiceHTTPResultResponse', ready, lambda q: q['outcome']['result']['recovery']['orders'][0].update(late_by_seconds=3600)),
]:
 q=deepcopy(value); mutate(q); assert list(validator(schema).iter_errors(q)), 'Invalid local voice HTTP semantic state accepted: ' + schema
precision=json.loads((ROOT/'examples/recovery_precision_request.json').read_text())
assert values['recovery_precision_session']['snapshot']==precision['snapshot'], 'Precision snapshot changed'
assert values['recovery_precision_turn']['call']['arguments']['assignments']==precision['recovery']['assignments'], 'Precision assignments changed'
print(str(len(mapping)) + ' local synthetic voice HTTP fixtures valid; identity/function/float/unknown/response-state mutations rejected')
