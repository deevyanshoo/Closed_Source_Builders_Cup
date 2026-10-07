#!/usr/bin/env python3
"""Deterministic recovery wire fixtures: schema and exact public numerical equality."""
import json
from pathlib import Path
import sys
from copy import deepcopy
from jsonschema import Draft202012Validator
ROOT=Path(__file__).resolve().parent
spec=json.loads((ROOT/'openapi.yaml').read_text())
assert '/v2/production/receipt-recovery/comparisons' in spec['paths'], 'Missing authenticated read-only recovery comparison contract'
def validator(name):
    return Draft202012Validator({'$ref':'#/components/schemas/'+name,'components':spec['components']})
for name in ('RecoveryComparisonRequest','RecoveryComparisonResponse','RecoveryComparisonUnsupportedResponse'):
    Draft202012Validator.check_schema(spec['components']['schemas'][name])
sys.path.insert(0,str(ROOT.parent/'procurement-engine/src'))
from procure.production.recovery import compare_receipt_recovery
for stem in ('recovery','recovery_noop','recovery_precision','recovery_unsupported'):
    request=json.loads((ROOT/'examples'/f'{stem}_request.json').read_text())
    response=json.loads((ROOT/'examples'/f'{stem}_response.json').read_text())
    validator('RecoveryComparisonRequest').validate(request)
    actual=compare_receipt_recovery(request['snapshot'],request['scenario'],request['recovery'])
    expected=({'contract_version':'0.2.0','comparison':actual} if actual['status']=='compared' else {'contract_version':'0.2.0',**actual})
    assert response==expected, 'Wire fixture differs from actual public numerical output'
    validator('RecoveryComparisonResponse' if actual['status']=='compared' else 'RecoveryComparisonUnsupportedResponse').validate(response)
request=json.loads((ROOT/'examples/recovery_request.json').read_text())
for mutate in (lambda q:q.update(unmodeled_constraint='0'),lambda q:q['snapshot']['inventory'][0].update(starting_stock=2.0),lambda q:q['scenario']['arrival_replacements'][0].update(quantity='4'),lambda q:q['recovery'].update(assignments=None)):
    q=deepcopy(request);mutate(q);assert list(validator('RecoveryComparisonRequest').iter_errors(q)), 'Invalid typed envelope accepted'
response=json.loads((ROOT/'examples/recovery_response.json').read_text())
for mutate in (lambda q:q['comparison'].update(synthetic=False),lambda q:q['comparison']['recovery']['orders'][0].update(late_by_seconds=3600),lambda q:q['comparison'].update(unknown='ignored')):
    q=deepcopy(response);mutate(q);assert list(validator('RecoveryComparisonResponse').iter_errors(q)), 'Invalid typed response accepted'
print('8 recovery fixtures match strict schema and actual public numerical output; malformed/float/unknown response cases rejected')
