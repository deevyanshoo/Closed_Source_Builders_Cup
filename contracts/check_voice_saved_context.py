#!/usr/bin/env python3
"""Strict stored synthetic context contracts, not a numerical acceptance test."""
import copy
import json
from pathlib import Path
from jsonschema import Draft202012Validator
ROOT = Path(__file__).resolve().parent
spec = json.loads((ROOT / 'openapi.yaml').read_text())
schemas = spec['components']['schemas']
required = ('ProductionContextMetadata','ProductionContextRef','StoredProductionContext','CreateProductionContextRequest','ReplaceProductionContextRequest','ProductionContextResponse','ProductionContextsResponse','VoiceHTTPSavedSessionRequest')
assert all(name in schemas for name in required), 'Saved production context contracts are absent'
def valid(value, name):
    validator = Draft202012Validator({'$ref':'#/components/schemas/'+name, 'components':spec['components']})
    return validator.is_valid(value)
for name in required:
    Draft202012Validator.check_schema(schemas[name])
for stem in ('recovery','recovery_noop','recovery_precision','recovery_unsupported'):
    snapshot = json.loads((ROOT / 'examples' / (stem+'_request.json')).read_text())['snapshot']
    body = {'contract_version':'0.2.0','snapshot':snapshot}
    assert valid(body,'CreateProductionContextRequest'), stem
    for change in (lambda b:b.update(metadata={'numerical_acceptance':'accepted'}),
                   lambda b:b['snapshot'].pop('products'), lambda b:b['snapshot'].update(synthetic=False),
                   lambda b:b['snapshot']['inventory'][0].update(starting_stock=1.234567890123456789)):
        bad=copy.deepcopy(body); change(bad); assert not valid(bad,'CreateProductionContextRequest')
ref={'context_id':'A'*32,'expected_revision':1,'contract_version':'0.2.0'}
assert valid(ref,'VoiceHTTPSavedSessionRequest')
for change in (lambda b:b.update(snapshot=snapshot), lambda b:b.update(expected_revision=0),
               lambda b:b.update(expected_revision=1.5),lambda b:b.update(expected_revision=2147483648)):
    bad=copy.deepcopy(ref); change(bad); assert not valid(bad,'VoiceHTTPSavedSessionRequest')
for name,schema in [('production_context_create','CreateProductionContextRequest'),('production_context_response','ProductionContextResponse'),('production_context_list','ProductionContextsResponse'),('voice_saved_context_session','VoiceHTTPSavedSessionRequest')]:
    assert valid(json.loads((ROOT/'examples'/(name+'.json')).read_text()),schema),name
# Legacy inline session fixtures remain valid; a stored reference cannot be pasted into them.
inline=json.loads((ROOT/'examples/voice_http_recovery_session.json').read_text())
assert valid(inline,'VoiceHTTPSessionRequest')
inline['context_id']='A'*32
assert not valid(inline,'VoiceHTTPSessionRequest')
print('Stored production context strict schemas/examples/decimal rejection and inline compatibility PASS')
