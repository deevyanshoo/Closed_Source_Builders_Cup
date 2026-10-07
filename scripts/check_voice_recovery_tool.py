#!/usr/bin/env python3
"""Actual local synthetic structured-tool composition; no speech/model/provider."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from procure.agents.voice_tools import TOOL_NAME, TrustedRecoveryContext, execute_recovery_tool, function_declaration
from procure.production.recovery import compare_receipt_recovery

ROOT = Path(__file__).resolve().parents[1]


def main():
    declaration = function_declaration()
    assert declaration['name'] == TOOL_NAME
    assert set(declaration['parameters']['properties']) == {'arrival_replacements', 'assignments'}
    checks = []
    for name in ('recovery', 'recovery_noop', 'recovery_precision', 'recovery_unsupported'):
        source = json.loads((ROOT / 'contracts/examples' / f'{name}_request.json').read_text())
        snapshot, scenario, recovery = source['snapshot'], source['scenario'], source['recovery']
        context = TrustedRecoveryContext(workspace_id=snapshot['workspace_id'], snapshot_id=snapshot['snapshot_id'],
            plan_version=snapshot['plan_version'], scenario_id=scenario['scenario_id'],
            session_id='synthetic-session', turn_id='synthetic-turn', snapshot=snapshot)
        arguments = {'arrival_replacements': scenario['arrival_replacements'], 'assignments': recovery['assignments']}
        call = json.dumps({'name': TOOL_NAME, 'arguments': arguments})
        active = {'turn': context.turn_id}
        guard = lambda session, turn: session == context.session_id and turn == active['turn']
        output = execute_recovery_tool(call, context, is_active=guard)
        expected = compare_receipt_recovery(snapshot, scenario, recovery)
        assert output['read_only'] is True
        assert output['status'] == expected['status']
        assert output['ownership'] == {'session_id': context.session_id, 'turn_id': context.turn_id}
        assert output['result'] == expected
        active['turn'] = 'replacement-turn'
        suppressed = execute_recovery_tool(call, context, is_active=guard)
        assert suppressed['status'] == 'suppressed' and 'result' not in suppressed
        checks.append({'example': name, 'status': output['status'], 'public_tool_equal': True,
                       'superseded_turn_suppressed': True,
                       'result_sha256': hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()})
    print(json.dumps({'evidence': 'actual local synthetic public-tool composition', 'provider': 'not_exercised',
                      'speech': 'not_exercised', 'authentication': 'host responsibility; synthetic test guard',
                      'checks': checks}, indent=2))


if __name__ == '__main__':
    main()
