#!/usr/bin/env python3
"""Check additive staging session semantics; no network or credentials."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
spec = json.loads((ROOT / 'openapi.yaml').read_text())
def validator(name):
    return Draft202012Validator({'$ref': '#/components/schemas/' + name, 'components': spec['components']})

session = validator('SessionResponse')
legacy = json.loads((ROOT / 'examples/session.json').read_text())
session.validate(legacy)
private = {**legacy, 'development_only': False, 'identity_mode': 'private_machine'}
session.validate(private)
for change in [
    {'development_only': False},
    {'development_only': False, 'identity_mode': 'development'},
    {'development_only': True, 'identity_mode': 'private_machine'},
    {'development_only': 'false', 'identity_mode': 'private_machine'},
]:
    value = {**legacy, **change}
    assert list(session.iter_errors(value)), 'Ambiguous session identity accepted: ' + repr(change)
validator('ReadinessResponse').validate({'contract_version': '0.2.0', 'process': 'ready', 'database': 'not_configured', 'identity': 'private_machine', 'provider': 'not_verified'})
print('Cloud foundation session modes and readiness contracts validated; legacy session preserved')
