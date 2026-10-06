#!/usr/bin/env python3
"""Validate fixtures against authoritative schemas, including exact decimal spelling."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent
spec = json.loads((ROOT / 'openapi.yaml').read_text())
mapping = {'request': 'PlanningRequest', 'precision': 'PlanningRequest', 'success': 'PlanResponse',
           'unsupported': 'PlanResponse', 'offer': 'OfferExample', 'voice': 'VoiceExample',
           'shipment': 'ShipmentExample', 'saved_plan': 'SavedPlanResponse',
           'approval': 'ApprovalResponse', 'action_request': 'ExecuteActionRequest',
           'action_receipt': 'ActionReceiptResponse', 'session': 'SessionResponse'}
for name, schema in mapping.items():
    value = json.loads((ROOT / 'examples' / (name + '.json')).read_text())
    validator = Draft202012Validator({'$ref': '#/components/schemas/' + schema,
                                     'components': spec['components']}, format_checker=FormatChecker())
    validator.validate(value)
precision = json.loads((ROOT / 'examples/precision.json').read_text())
assert precision['budget']['amount'] == '12345678901234567890.123456789012345678'
assert precision['quantity']['amount'] == '9007199254740993.123456789012345678'
print(str(len(mapping)) + ' exact fixtures validated against OpenAPI schemas')
