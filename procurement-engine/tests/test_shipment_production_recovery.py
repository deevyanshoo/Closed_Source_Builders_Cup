"""Private synthetic shipment-to-production composition acceptance checks."""
from __future__ import annotations

from copy import deepcopy
from decimal import localcontext
import hashlib
import json
from pathlib import Path
import pytest

import procure.production.shipment_recovery as adapter

from procure.events.ingest import bind_synthetic_milestones, build_arrival_scenarios
from procure.production.recovery import compare_receipt_recovery
from procure.production.shipment_recovery import compare_shipment_production_recovery


EXAMPLES = Path(__file__).resolve().parents[2] / 'contracts' / 'examples'
AS_OF = '2026-10-09T00:00:00Z'
P1 = '2026-10-10T09:00:00Z'
P2 = '2026-10-10T10:00:00Z'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def case(*, eta=P2):
    production = json.loads((EXAMPLES / 'recovery_request.json').read_text())['snapshot']
    graph = {
        'workspace_id': 'W', 'plan_version': 'v7', 'snapshot_id': 'graph-1',
        'as_of': AS_OF, 'synthetic': True,
        'nodes': [
            {'id': 'SH', 'kind': 'shipment'}, {'id': 'PO', 'kind': 'purchase_order'},
            {'id': 'C', 'kind': 'component'}, {'id': 'O-A', 'kind': 'production_order'},
            {'id': 'K', 'kind': 'commitment'},
        ],
        'edges': [
            {'from': 'SH', 'to': 'PO'}, {'from': 'PO', 'to': 'C'},
            {'from': 'C', 'to': 'O-A'}, {'from': 'O-A', 'to': 'K'},
        ],
        'inventory': [{'component_id': 'C', 'unit': 'kg', 'starting_stock': '2'}],
        'arrival_lines': [{'line_id': 'L1', 'shipment_id': 'SH', 'purchase_order_id': 'PO',
                           'component_id': 'C', 'unit': 'kg', 'quantity': '4', 'arrival_at': P1}],
        'commitments': [{'commitment_id': 'K', 'production_order_id': 'O-A',
                         'required_at': P1, 'due_at': P2,
                         'demands': [{'component_id': 'C', 'unit': 'kg', 'quantity': '6'}]}],
    }
    raw = [{
        'event_id': 'event-1', 'shipment_id': 'SH', 'type': 'eta_changed',
        'occurred_at': '2026-10-08T01:00:00Z', 'observed_at': '2026-10-08T01:01:00Z',
        'eta': eta, 'synthetic': True,
        'lane': {'origin': 'AEJEA', 'destination': 'INMAA', 'mode': 'ocean'},
        'source': {'provider_id': 'synthetic-carrier', 'kind': 'carrier_feed'},
        'evidence': [{'evidence_id': 'e1', 'kind': 'carrier_status',
                      'locator': 'synthetic://status/1', 'content_sha256': 'a' * 64,
                      'retrieved_at': '2026-10-08T01:01:00Z', 'valid_until': None}],
    }]
    request = {'adapter_version': '1', 'raw_records': raw, 'propagation_snapshot': graph,
               'production_snapshot': production, 'joins': [{'line_id': 'L1', 'receipt_id': 'r-C'}],
               'recovery_assignments': [{'order_id': 'O-A', 'period_id': 'P2'},
                                        {'order_id': 'O-B', 'period_id': 'P2'},
                                        {'order_id': 'O-U', 'period_id': 'P1'}]}
    return request, binding(request)


def binding(request):
    graph = request['propagation_snapshot']
    production = request['production_snapshot']
    return {'adapter_version': '1', 'synthetic': True,
            'workspace_id': 'W', 'plan_version': 'v7',
            'propagation_snapshot_id': graph['snapshot_id'],
            'production_snapshot_id': production['snapshot_id'], 'as_of': AS_OF,
            'raw_sha256': digest(request['raw_records']),
            'propagation_sha256': digest(graph),
            'production_sha256': digest(production),
            'joins_sha256': digest(request['joins']),
            'assignments_sha256': digest(request['recovery_assignments'])}


def test_delayed_fixture_is_eligible_at_authoritative_cutoff():
    request, _ = case()
    graph = request['propagation_snapshot']
    bound = bind_synthetic_milestones(request['raw_records'], graph,
                                      expected_workspace_id='W', expected_plan_version='v7',
                                      expected_snapshot_id='graph-1')
    evidence = build_arrival_scenarios(bound, graph)
    assert len(evidence['scenarios']) == 1
    assert evidence['scenarios'][0]['shipment_id'] == 'SH'


def test_delayed_event_maps_to_entire_unchanged_public_comparison():
    request, expected_binding = case()
    original = deepcopy(request)
    result = compare_shipment_production_recovery(request, expected_binding=expected_binding)
    assert result['status'] == 'evaluated', result
    assert request == original
    assert result['binding']['binding_sha256'] == digest(expected_binding)
    assert len(result['comparisons']) == 1
    row = result['comparisons'][0]
    assert row['shipment_id'] == 'SH'
    assert row['projection']['status'] == 'screened'
    assert row['receipt_scenario']['arrival_replacements'] == [
        {'receipt_id': 'r-C', 'expected_arrival_at': P1, 'arrival_at': P2.replace('Z', '+00:00')}]
    expected = compare_receipt_recovery(request['production_snapshot'],
                                        row['receipt_scenario'], row['recovery'])
    assert row['result'] == expected
    assert digest(row['result']) == digest(expected)
    assert row['result']['disrupted_baseline']['orders'][0]['demands'][0]['shortfall'] == '4'
    assert row['result']['recovery']['orders'][0]['late_by_seconds'] == '3600'


def assert_whole_public_equality(request, expected_binding, *, status='compared'):
    outcome = compare_shipment_production_recovery(request, expected_binding=expected_binding)
    assert outcome['status'] == 'evaluated', outcome
    assert len(outcome['comparisons']) == 1
    row = outcome['comparisons'][0]
    expected = compare_receipt_recovery(request['production_snapshot'],
                                        row['receipt_scenario'], row['recovery'])
    assert row['result'] == expected
    assert digest(row['result']) == digest(expected)
    assert row['result']['status'] == status
    return outcome, row


def test_eligible_same_date_keeps_noop_replacement_and_whole_public_result():
    request, _ = case(eta=P1)
    request['recovery_assignments'] = deepcopy(request['production_snapshot']['baseline_assignments'])
    outcome, row = assert_whole_public_equality(request, binding(request))
    assert row['receipt_scenario']['arrival_replacements'][0]['receipt_id'] == 'r-C'
    assert row['result']['original_baseline'] == row['result']['disrupted_baseline'] == row['result']['recovery']
    assert row['result']['receipt_changes'] == []
    assert outcome['evidence']['scenarios'][0]['source_receipt']['event']['record_index'] == 0


def test_precision_preserves_whole_public_result_under_low_decimal_context():
    request, _ = case()
    exact = '4.123456789123456789'
    request['propagation_snapshot']['arrival_lines'][0]['quantity'] = exact
    request['production_snapshot']['receipts'][0]['quantity'] = exact
    stock = '2.123456789123456789'
    request['propagation_snapshot']['inventory'][0]['starting_stock'] = stock
    request['production_snapshot']['inventory'][0]['starting_stock'] = stock
    with localcontext() as context:
        context.prec = 3
        _, row = assert_whole_public_equality(request, binding(request))
    assert row['result']['disrupted_baseline']['orders'][0]['demands'][0]['shortfall'] == '3.876543210876543211'


def test_derived_numeric_overflow_preserves_whole_public_unsupported():
    request, _ = case()
    huge = '9' * 1024
    request['production_snapshot']['orders'][0]['output_quantity'] = huge
    request['production_snapshot']['orders'][1]['output_quantity'] = huge
    request['production_snapshot']['routings'][0]['run_seconds_per_output_unit'] = huge
    _, row = assert_whole_public_equality(request, binding(request), status='unsupported')
    assert row['result']['reason'] == 'invalid_quantity'


def test_empty_source_keeps_factory_evidence_and_calls_no_numerical_tools(monkeypatch):
    request, _ = case()
    request['raw_records'] = []
    calls = []
    monkeypatch.setattr(adapter, 'project_arrival_change', lambda *_: calls.append('projection'))
    monkeypatch.setattr(adapter, 'compare_receipt_recovery', lambda *_: calls.append('comparison'))
    result = compare_shipment_production_recovery(request, expected_binding=binding(request))
    assert result['status'] == 'no_eligible_scenario', result
    assert result['evidence'] == {'scenarios': [], 'excluded': [],
                                  'normalization_rejected': [], 'normalization_duplicates': []}
    assert result['comparisons'] == []
    assert calls == []


@pytest.mark.parametrize('change,stage,reason', [
    (lambda r: r['joins'].clear(), 'join', 'invalid_join'),
    (lambda r: r['joins'].append(deepcopy(r['joins'][0])), 'join', 'invalid_join'),
    (lambda r: r['joins'][0].update(receipt_id='r-foreign'), 'join', 'invalid_join'),
    (lambda r: r['joins'][0].update(extra='x'), 'join', 'invalid_join'),
    (lambda r: r['propagation_snapshot']['arrival_lines'][0].update(unit='each'), 'join', 'inconsistent_join'),
    (lambda r: r['propagation_snapshot']['arrival_lines'][0].update(quantity='5'), 'join', 'inconsistent_join'),
    (lambda r: r['propagation_snapshot']['arrival_lines'][0].update(arrival_at=P2), 'join', 'inconsistent_join'),
    (lambda r: r['propagation_snapshot']['inventory'][0].update(starting_stock='3'), 'join', 'inconsistent_join'),
    (lambda r: r['production_snapshot']['inventory'][0].update(stock_as_of=P1), 'join', 'inconsistent_join'),
    (lambda r: r['recovery_assignments'].pop(), 'join', 'incomplete_assignments'),
    (lambda r: r['recovery_assignments'][0].update(period_id='foreign'), 'join', 'incomplete_assignments'),
])
def test_invalid_join_or_assignment_is_atomic_and_precedes_math(monkeypatch, change, stage, reason):
    request, _ = case()
    change(request)
    calls = []
    monkeypatch.setattr(adapter, 'project_arrival_change', lambda *_: calls.append('projection'))
    monkeypatch.setattr(adapter, 'compare_receipt_recovery', lambda *_: calls.append('comparison'))
    result = compare_shipment_production_recovery(request, expected_binding=binding(request))
    assert (result['status'], result['stage'], result['reason']) == ('rejected', stage, reason), result
    assert result['evidence'] is None and result['comparisons'] == []
    assert calls == []


def test_every_unique_line_must_join_even_without_eligible_source():
    request, _ = case()
    line = deepcopy(request['propagation_snapshot']['arrival_lines'][0])
    line['line_id'], line['shipment_id'] = 'L2', 'SH-OTHER'
    request['propagation_snapshot']['arrival_lines'].append(line)
    result = compare_shipment_production_recovery(request, expected_binding=binding(request))
    assert (result['stage'], result['reason']) == ('join', 'invalid_join')


def test_identical_repeated_source_lines_receipts_and_orders_coalesce():
    request, _ = case()
    request['propagation_snapshot']['arrival_lines'].append(
        deepcopy(request['propagation_snapshot']['arrival_lines'][0]))
    request['production_snapshot']['receipts'].append(
        deepcopy(request['production_snapshot']['receipts'][0]))
    request['production_snapshot']['orders'].append(
        deepcopy(request['production_snapshot']['orders'][0]))
    _, row = assert_whole_public_equality(request, binding(request))
    assert len(row['receipt_scenario']['arrival_replacements']) == 1


def test_conflicting_logical_source_or_receipt_copy_rejects():
    for name, field in [('arrival_lines', 'quantity'), ('receipts', 'quantity')]:
        request, _ = case()
        parent = request['propagation_snapshot'] if name == 'arrival_lines' else request['production_snapshot']
        duplicate = deepcopy(parent[name][0])
        duplicate[field] = '5'
        parent[name].append(duplicate)
        result = compare_shipment_production_recovery(request, expected_binding=binding(request))
        assert (result['stage'], result['reason']) == ('join', 'inconsistent_join')


def test_stale_whole_value_binding_rejects_before_math(monkeypatch):
    request, old_binding = case()
    request['production_snapshot']['maintenance'][0]['start'] = '2026-10-10T09:01:00Z'
    calls = []
    monkeypatch.setattr(adapter, 'project_arrival_change', lambda *_: calls.append('projection'))
    monkeypatch.setattr(adapter, 'compare_receipt_recovery', lambda *_: calls.append('comparison'))
    result = compare_shipment_production_recovery(request, expected_binding=old_binding)
    assert (result['stage'], result['reason']) == ('binding', 'binding_mismatch')
    assert calls == []


def test_projection_of_all_sources_precedes_any_public_comparison(monkeypatch):
    request, _ = case()
    another = deepcopy(request['raw_records'][0])
    another['event_id'], another['shipment_id'] = 'event-2', 'SH-OTHER'
    request['raw_records'].append(another)
    line = deepcopy(request['propagation_snapshot']['arrival_lines'][0])
    line['line_id'], line['shipment_id'] = 'L2', 'SH-OTHER'
    request['propagation_snapshot']['arrival_lines'].append(line)
    request['propagation_snapshot']['nodes'].append({'id': 'SH-OTHER', 'kind': 'shipment'})
    request['propagation_snapshot']['edges'].append({'from': 'SH-OTHER', 'to': 'PO'})
    receipt = deepcopy(request['production_snapshot']['receipts'][0])
    receipt['receipt_id'] = 'r-C2'
    request['production_snapshot']['receipts'].append(receipt)
    request['joins'].append({'line_id': 'L2', 'receipt_id': 'r-C2'})
    calls = []
    real_project = adapter.project_arrival_change

    def fail_second(graph, scenario):
        calls.append('projection')
        if len(calls) == 2:
            return {'status': 'unsupported', 'reason': 'test', 'details': []}
        return real_project(graph, scenario)

    monkeypatch.setattr(adapter, 'project_arrival_change', fail_second)
    monkeypatch.setattr(adapter, 'compare_receipt_recovery', lambda *_: calls.append('comparison'))
    result = compare_shipment_production_recovery(request, expected_binding=binding(request))
    assert (result['stage'], result['reason']) == ('projection', 'unsupported_propagation')
    assert len(result['evidence']['scenarios']) == 2 and result['comparisons'] == []
    assert calls == ['projection', 'projection']


@pytest.mark.parametrize('edit,place,reason', [
    (lambda r: r['raw_records'][0].update(observed_at='2026-10-09T01:00:00Z'),
     'normalization_rejected', 'observed_after_as_of'),
    (lambda r: r['raw_records'][0]['evidence'][0].update(retrieved_at='2026-10-09T01:00:00Z'),
     'normalization_rejected', 'evidence_after_as_of'),
    (lambda r: r['raw_records'][0]['evidence'][0].update(valid_until='2026-10-08T02:00:00Z'),
     'excluded', 'evidence_expired'),
    (lambda r: (r['raw_records'][0].update(type='port_hold'), r['raw_records'][0].pop('eta')),
     'excluded', 'hold_without_new_eta'),
    (lambda r: (r['raw_records'][0].update(type='partially_delivered'),
                r['raw_records'][0].update(quantity={'amount': '2', 'unit': 'kg'})),
     'excluded', 'partial_quantity_not_supported'),
])
def test_noneligible_source_evidence_survives_without_math(monkeypatch, edit, place, reason):
    request, _ = case()
    edit(request)
    calls = []
    monkeypatch.setattr(adapter, 'project_arrival_change', lambda *_: calls.append('projection'))
    monkeypatch.setattr(adapter, 'compare_receipt_recovery', lambda *_: calls.append('comparison'))
    result = compare_shipment_production_recovery(request, expected_binding=binding(request))
    assert result['status'] == 'no_eligible_scenario', result
    assert [row['reason'] for row in result['evidence'][place]] == [reason]
    assert result['comparisons'] == [] and calls == []


def test_corrected_source_changes_bound_ids_but_keeps_numerical_result():
    request, _ = case()
    first, first_row = assert_whole_public_equality(request, binding(request))
    corrected = deepcopy(request)
    corrected['raw_records'][0]['event_id'] = 'event-corrected'
    second, second_row = assert_whole_public_equality(corrected, binding(corrected))
    assert first['evidence']['scenarios'][0]['source_receipt'] != second['evidence']['scenarios'][0]['source_receipt']
    assert first_row['source_scenario_id'] != second_row['source_scenario_id']
    assert first_row['receipt_scenario']['scenario_id'] != second_row['receipt_scenario']['scenario_id']
    for name in ('original_baseline', 'disrupted_baseline', 'recovery', 'receipt_changes'):
        assert first_row['result'][name] == second_row['result'][name]


def test_equivalent_utc_cutoffs_and_original_arrival_instants_are_joined():
    request, _ = case()
    request['propagation_snapshot']['as_of'] = '2026-10-09T05:30:00+05:30'
    request['production_snapshot']['as_of'] = '2026-10-09T05:30:00+05:30'
    request['production_snapshot']['inventory'][0]['stock_as_of'] = '2026-10-09T05:30:00+05:30'
    request['propagation_snapshot']['arrival_lines'][0]['arrival_at'] = '2026-10-10T14:30:00+05:30'
    _, row = assert_whole_public_equality(request, binding(request))
    assert row['receipt_scenario']['arrival_replacements'][0]['expected_arrival_at'] == P1


@pytest.mark.parametrize('mutate,stage,reason', [
    (lambda r: r.update(extra='x'), 'input', 'invalid_input'),
    (lambda r: r['raw_records'][0].update(authority='fake'), 'input', 'invalid_input'),
    (lambda r: r['raw_records'][0]['evidence'][0].update(authority='fake'), 'input', 'invalid_input'),
    (lambda r: r['raw_records'][0].update(number=1), 'input', 'invalid_input'),
    (lambda r: r['raw_records'][0].update(source={'provider_id': 1}), 'input', 'invalid_input'),
    (lambda r: r['propagation_snapshot'].update(notice='\ud800'), 'input', 'invalid_input'),
    (lambda r: r['propagation_snapshot'].update(notice='x' * 131073), 'input', 'input_limit'),
    (lambda r: r['raw_records'][0].update(evidence=[{}] * 257), 'input', 'input_limit'),
])
def test_invalid_plain_json_or_unknown_authority_rejected_precopy(mutate, stage, reason):
    request, bound = case()
    mutate(request)
    result = compare_shipment_production_recovery(request, expected_binding=bound)
    assert (result['stage'], result['reason']) == (stage, reason)
    assert result['comparisons'] == []


def test_cyclic_or_too_deep_input_is_rejected_before_copy():
    request, bound = case()
    request['propagation_snapshot']['notice'] = []
    request['propagation_snapshot']['notice'].append(request['propagation_snapshot']['notice'])
    result = compare_shipment_production_recovery(request, expected_binding=bound)
    assert (result['stage'], result['reason']) == ('input', 'invalid_input')
    request, bound = case()
    nested = ['x']
    for _ in range(17):
        nested = [nested]
    request['propagation_snapshot']['notice'] = nested
    result = compare_shipment_production_recovery(request, expected_binding=bound)
    assert (result['stage'], result['reason']) == ('input', 'invalid_input')


def test_binding_scope_and_each_full_hash_rejects_stale_assertions():
    for key in ('raw_sha256', 'propagation_sha256', 'production_sha256',
                'joins_sha256', 'assignments_sha256', 'workspace_id', 'plan_version',
                'propagation_snapshot_id', 'production_snapshot_id', 'as_of'):
        request, bound = case()
        bound[key] = ('b' * 64 if key.endswith('_sha256') else 'wrong')
        result = compare_shipment_production_recovery(request, expected_binding=bound)
        assert (result['stage'], result['reason']) == ('binding', 'binding_mismatch'), key


def test_runtime_failure_and_output_overflow_are_atomic(monkeypatch):
    request, bound = case()
    monkeypatch.setattr(adapter, 'compare_receipt_recovery', lambda *_: (_ for _ in ()).throw(RuntimeError('secret')))
    result = compare_shipment_production_recovery(request, expected_binding=bound)
    assert (result['stage'], result['reason']) == ('runtime', 'comparison_unavailable')
    assert result['evidence'] is not None and result['comparisons'] == []
    monkeypatch.undo()
    monkeypatch.setattr(adapter, '_OUTPUT_LIMIT', 256)
    result = compare_shipment_production_recovery(request, expected_binding=bound)
    assert (result['stage'], result['reason']) == ('output', 'output_limit')
    assert result['comparisons'] == []


def test_no_eligible_still_requires_declared_graph_row_shapes():
    for name in ('nodes', 'edges', 'inventory', 'arrival_lines', 'commitments'):
        request, _ = case()
        request['raw_records'] = []
        request['propagation_snapshot'][name].append(None)
        result = compare_shipment_production_recovery(request, expected_binding=binding(request))
        assert (result['stage'], result['reason']) == ('input', 'invalid_input'), name
    request, _ = case()
    request['raw_records'] = []
    request['propagation_snapshot']['commitments'][0]['demands'].append(None)
    result = compare_shipment_production_recovery(request, expected_binding=binding(request))
    assert (result['stage'], result['reason']) == ('input', 'invalid_input')


def test_join_accepts_valid_1024_digit_quantity_and_stock():
    request, _ = case()
    huge = '9' * 1024
    request['propagation_snapshot']['arrival_lines'][0]['quantity'] = huge
    request['production_snapshot']['receipts'][0]['quantity'] = huge
    request['propagation_snapshot']['inventory'][0]['starting_stock'] = huge
    request['production_snapshot']['inventory'][0]['starting_stock'] = huge
    outcome = compare_shipment_production_recovery(request, expected_binding=binding(request))
    assert outcome['status'] == 'evaluated', outcome
    row = outcome['comparisons'][0]
    assert row['result'] == compare_receipt_recovery(request['production_snapshot'],
                                                      row['receipt_scenario'], row['recovery'])


def test_repeated_input_is_deterministic_and_immutable():
    request, bound = case()
    before = deepcopy((request, bound))
    first = compare_shipment_production_recovery(request, expected_binding=bound)
    second = compare_shipment_production_recovery(request, expected_binding=bound)
    assert first == second
    assert (request, bound) == before


def test_invalid_public_result_is_rejected_atomically(monkeypatch):
    request, bound = case()
    monkeypatch.setattr(adapter, 'compare_receipt_recovery', lambda *_: {'status': 'compared'})
    result = compare_shipment_production_recovery(request, expected_binding=bound)
    assert (result['stage'], result['reason']) == ('output', 'invalid_result')
    assert result['comparisons'] == [] and result['evidence'] is not None


def test_bound_size_is_checked_before_ownership_copy(monkeypatch):
    request, bound = case()
    request['propagation_snapshot']['notice'] = 'x' * 131073
    original_loads = adapter.json.loads
    calls = []

    def track_loads(value, *args, **kwargs):
        calls.append(1)
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(adapter.json, 'loads', track_loads)
    result = compare_shipment_production_recovery(request, expected_binding=bound)
    assert (result['stage'], result['reason']) == ('input', 'input_limit')
    assert calls == []


def test_two_eligible_sources_stay_independent_and_sorted():
    request, _ = case()
    second = deepcopy(request['raw_records'][0])
    second['event_id'], second['shipment_id'] = 'event-2', 'SH-OTHER'
    request['raw_records'].insert(0, second)
    graph = request['propagation_snapshot']
    graph['nodes'].append({'id': 'SH-OTHER', 'kind': 'shipment'})
    graph['edges'].append({'from': 'SH-OTHER', 'to': 'PO'})
    line = deepcopy(graph['arrival_lines'][0])
    line['line_id'], line['shipment_id'] = 'L2', 'SH-OTHER'
    graph['arrival_lines'].append(line)
    receipt = deepcopy(request['production_snapshot']['receipts'][0])
    receipt['receipt_id'] = 'r-C2'
    request['production_snapshot']['receipts'].append(receipt)
    request['joins'].append({'line_id': 'L2', 'receipt_id': 'r-C2'})
    outcome = compare_shipment_production_recovery(request, expected_binding=binding(request))
    assert outcome['status'] == 'evaluated', outcome
    assert [row['shipment_id'] for row in outcome['comparisons']] == ['SH', 'SH-OTHER']
    assert [row['receipt_scenario']['arrival_replacements'][0]['receipt_id']
            for row in outcome['comparisons']] == ['r-C', 'r-C2']
    for row in outcome['comparisons']:
        assert row['result'] == compare_receipt_recovery(request['production_snapshot'],
                                                          row['receipt_scenario'], row['recovery'])
    assert (outcome['comparisons'][0]['receipt_scenario']['scenario_id'] !=
            outcome['comparisons'][1]['receipt_scenario']['scenario_id'])


def test_corrected_reference_and_content_same_event_rebind_source_and_composition():
    request, original_binding = case()
    first, first_row = assert_whole_public_equality(request, original_binding)
    corrected = deepcopy(request)
    record = corrected['raw_records'][0]
    record['source']['reference'] = 'corrected-reference'
    record['evidence'][0]['content_sha256'] = 'b' * 64
    assert record['event_id'] == request['raw_records'][0]['event_id']
    stale = compare_shipment_production_recovery(corrected, expected_binding=original_binding)
    assert (stale['stage'], stale['reason']) == ('binding', 'binding_mismatch')
    second, second_row = assert_whole_public_equality(corrected, binding(corrected))
    assert first_row['source_scenario_id'] != second_row['source_scenario_id']
    assert first_row['receipt_scenario']['scenario_id'] != second_row['receipt_scenario']['scenario_id']
    assert (first['evidence']['scenarios'][0]['source_receipt'] !=
            second['evidence']['scenarios'][0]['source_receipt'])
    for key in ('original_baseline', 'disrupted_baseline', 'recovery', 'receipt_changes'):
        assert first_row['result'][key] == second_row['result'][key]


def test_valid_duplicate_invalid_and_expired_raw_records_keep_entire_factory_envelope():
    request, _ = case()
    valid = request['raw_records'][0]
    duplicate = deepcopy(valid)
    future = deepcopy(valid)
    future['event_id'] = 'future'
    future['observed_at'] = '2026-10-09T01:00:00Z'
    expired = deepcopy(valid)
    expired['event_id'] = 'expired'
    expired['evidence'][0]['valid_until'] = '2026-10-08T02:00:00Z'
    request['raw_records'] = [valid, duplicate, future, expired]
    graph = request['propagation_snapshot']
    direct = build_arrival_scenarios(bind_synthetic_milestones(
        request['raw_records'], graph,
        expected_workspace_id='W', expected_plan_version='v7',
        expected_snapshot_id='graph-1'), graph)
    outcome, row = assert_whole_public_equality(request, binding(request))
    assert outcome['evidence'] == direct
    assert direct['normalization_duplicates']
    assert [x['reason'] for x in direct['normalization_rejected']] == ['observed_after_as_of']
    assert [x['reason'] for x in direct['excluded']] == ['evidence_expired']
    assert row['result']['status'] == 'compared'


def test_raw_array_permutation_needs_fresh_guard_and_rebinds_ids_without_numerical_change():
    request, _ = case()
    earlier = deepcopy(request['raw_records'][0])
    earlier['event_id'] = 'earlier'
    earlier['occurred_at'] = '2026-10-08T00:00:00Z'
    earlier['observed_at'] = '2026-10-08T00:01:00Z'
    earlier['evidence'][0]['retrieved_at'] = '2026-10-08T00:01:00Z'
    request['raw_records'].append(earlier)
    original_binding = binding(request)
    _, first = assert_whole_public_equality(request, original_binding)
    permuted = deepcopy(request)
    permuted['raw_records'].reverse()
    stale = compare_shipment_production_recovery(permuted, expected_binding=original_binding)
    assert (stale['stage'], stale['reason']) == ('binding', 'binding_mismatch')
    _, second = assert_whole_public_equality(permuted, binding(permuted))
    assert first['source_scenario_id'] != second['source_scenario_id']
    assert first['receipt_scenario']['scenario_id'] != second['receipt_scenario']['scenario_id']
    for key in ('original_baseline', 'disrupted_baseline', 'recovery', 'receipt_changes'):
        assert first['result'][key] == second['result'][key]


@pytest.mark.parametrize('part', ['raw', 'request', 'binding', 'entries'])
def test_each_aggregate_byte_or_entry_cap_precedes_ownership_copy(monkeypatch, part):
    request, bound = case()
    if part == 'raw':
        request['raw_records'] = [deepcopy(request['raw_records'][0]) for _ in range(9)]
        for row in request['raw_records']:
            row['evidence'][0]['locator'] = 'x' * 120000
    elif part == 'request':
        request['production_snapshot']['padding'] = {f'p{i}': 'x' * 120000 for i in range(18)}
    elif part == 'binding':
        bound['workspace_id'] = 'x' * 8192
    else:
        request['production_snapshot']['padding'] = [['x'] * 256 for _ in range(65)]
    calls = []
    real_loads = adapter.json.loads

    def track_loads(value, *args, **kwargs):
        calls.append(1)
        return real_loads(value, *args, **kwargs)

    monkeypatch.setattr(adapter.json, 'loads', track_loads)
    result = compare_shipment_production_recovery(request, expected_binding=bound)
    assert (result['stage'], result['reason']) == (
        ('binding', 'binding_mismatch') if part == 'binding' else ('input', 'input_limit'))
    assert calls == []


def test_seventeen_real_eligible_scenarios_reject_before_numerical_calls(monkeypatch):
    request, _ = case()
    for index in range(1, 17):
        shipment, line_id, receipt_id = f'SH-{index}', f'L-{index}', f'r-C-{index}'
        record = deepcopy(request['raw_records'][0])
        record['event_id'], record['shipment_id'] = f'other-event-{index}', shipment
        request['raw_records'].append(record)
        graph = request['propagation_snapshot']
        graph['nodes'].append({'id': shipment, 'kind': 'shipment'})
        graph['edges'].append({'from': shipment, 'to': 'PO'})
        line = deepcopy(graph['arrival_lines'][0])
        line['line_id'], line['shipment_id'] = line_id, shipment
        graph['arrival_lines'].append(line)
        receipt = deepcopy(request['production_snapshot']['receipts'][0])
        receipt['receipt_id'] = receipt_id
        request['production_snapshot']['receipts'].append(receipt)
        request['joins'].append({'line_id': line_id, 'receipt_id': receipt_id})
    calls = []
    monkeypatch.setattr(adapter, 'project_arrival_change', lambda *_: calls.append('projection'))
    monkeypatch.setattr(adapter, 'compare_receipt_recovery', lambda *_: calls.append('comparison'))
    result = compare_shipment_production_recovery(request, expected_binding=binding(request))
    assert (result['stage'], result['reason']) == ('input', 'input_limit'), result
    assert len(result['evidence']['scenarios']) == 17
    assert result['comparisons'] == [] and calls == []


def test_second_real_public_call_failure_discards_first_completed_row(monkeypatch):
    request, _ = case()
    record = deepcopy(request['raw_records'][0])
    record['event_id'], record['shipment_id'] = 'event-2', 'SH-OTHER'
    request['raw_records'].append(record)
    graph = request['propagation_snapshot']
    graph['nodes'].append({'id': 'SH-OTHER', 'kind': 'shipment'})
    graph['edges'].append({'from': 'SH-OTHER', 'to': 'PO'})
    line = deepcopy(graph['arrival_lines'][0])
    line['line_id'], line['shipment_id'] = 'L2', 'SH-OTHER'
    graph['arrival_lines'].append(line)
    receipt = deepcopy(request['production_snapshot']['receipts'][0])
    receipt['receipt_id'] = 'r-C2'
    request['production_snapshot']['receipts'].append(receipt)
    request['joins'].append({'line_id': 'L2', 'receipt_id': 'r-C2'})
    real_compare = adapter.compare_receipt_recovery
    calls = []

    def fail_after_real_first(snapshot, scenario, recovery):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('hidden')
        return real_compare(snapshot, scenario, recovery)

    monkeypatch.setattr(adapter, 'compare_receipt_recovery', fail_after_real_first)
    result = compare_shipment_production_recovery(request, expected_binding=binding(request))
    assert (result['stage'], result['reason']) == ('runtime', 'comparison_unavailable')
    assert len(result['evidence']['scenarios']) == 2
    assert result['comparisons'] == [] and calls == [1, 1]
