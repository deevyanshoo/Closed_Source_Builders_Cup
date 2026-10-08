"""Private synthetic shipment evidence to independent production comparisons."""
from __future__ import annotations

import hashlib
import json
import re

from procure.events.ingest import bind_synthetic_milestones, build_arrival_scenarios
from procure.events.propagation import project_arrival_change
from procure.production._exact import ScreenError, amount, instant
from procure.production.http_comparison import _DEFINITIONS, _valid
from procure.production.recovery import compare_receipt_recovery


_REQUEST_KEYS = frozenset(('adapter_version', 'raw_records', 'propagation_snapshot',
                           'production_snapshot', 'joins', 'recovery_assignments'))
_BINDING_KEYS = frozenset(('adapter_version', 'synthetic', 'workspace_id', 'plan_version',
                           'propagation_snapshot_id', 'production_snapshot_id', 'as_of',
                           'raw_sha256', 'propagation_sha256', 'production_sha256',
                           'joins_sha256', 'assignments_sha256'))
_GRAPH_KEYS = frozenset(('workspace_id', 'plan_version', 'snapshot_id', 'as_of',
                         'synthetic', 'nodes', 'edges', 'inventory', 'arrival_lines',
                         'commitments', 'notice'))
_RAW_KEYS = frozenset(('event_id', 'shipment_id', 'type', 'occurred_at', 'observed_at',
                       'eta', 'synthetic', 'facility', 'lane', 'source', 'evidence', 'quantity'))
_RAW_NESTED = {
    'facility': frozenset(('kind', 'code', 'name')),
    'lane': frozenset(('origin', 'destination', 'mode', 'name')),
    'source': frozenset(('provider_id', 'kind', 'reference')),
    'quantity': frozenset(('amount', 'unit')),
    'evidence': frozenset(('evidence_id', 'kind', 'locator', 'content_sha256',
                           'retrieved_at', 'valid_until')),
}
_HASH = re.compile(r'^[0-9a-f]{64}$')
_INPUT_LIMIT, _PART_LIMIT, _BINDING_LIMIT, _OUTPUT_LIMIT = 2 << 20, 1 << 20, 8 << 10, 4 << 20
_STRING_LIMIT, _DEPTH_LIMIT, _ARRAY_LIMIT, _ENTRY_LIMIT = 128 << 10, 16, 256, 16384


class _Abort(Exception):
    def __init__(self, stage: str, reason: str, evidence=None):
        self.stage, self.reason, self.evidence = stage, reason, evidence


def _bytes(value, limit: int, stage: str) -> bytes:
    """Validate and bound exact plain JSON before ownership copying."""
    seen: set[int] = set()
    entries = [0]
    minimum_size = [0]
    invalid = 'invalid_input' if stage == 'input' else 'binding_mismatch'
    oversized = 'input_limit' if stage == 'input' else 'binding_mismatch'

    def visit(item, depth: int):
        if depth > _DEPTH_LIMIT:
            raise _Abort(stage, invalid)
        if type(item) is str:
            if len(item) > _STRING_LIMIT:
                raise _Abort(stage, oversized)
            try:
                size = len(item.encode('utf-8'))
            except UnicodeError:
                raise _Abort(stage, invalid) from None
            if size > _STRING_LIMIT:
                raise _Abort(stage, oversized)
            minimum_size[0] += size
            if minimum_size[0] > limit:
                raise _Abort(stage, oversized)
            return
        if item is None or type(item) is bool:
            minimum_size[0] += 1
            return
        if type(item) not in (dict, list):
            raise _Abort(stage, invalid)
        identity = id(item)
        if identity in seen:
            raise _Abort(stage, invalid)
        seen.add(identity)
        try:
            if type(item) is list and len(item) > _ARRAY_LIMIT:
                raise _Abort(stage, oversized)
            entries[0] += len(item)
            if entries[0] > _ENTRY_LIMIT:
                raise _Abort(stage, oversized)
            if type(item) is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise _Abort(stage, invalid)
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
        finally:
            seen.remove(identity)

    try:
        visit(value, 0)
        chunks = []
        size = 0
        encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=False, allow_nan=False)
        for chunk in encoder.iterencode(value):
            item = chunk.encode('utf-8')
            size += len(item)
            if size > limit:
                raise _Abort(stage, oversized)
            chunks.append(item)
        encoded = b''.join(chunks)
    except _Abort:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise _Abort(stage, invalid) from None
    if len(encoded) > limit:
        raise _Abort(stage, oversized)
    return encoded


def _hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _id(value) -> bool:
    return type(value) is str and 0 < len(value) <= 256 and value == value.strip()


def _output_bytes(value) -> bytes:
    chunks, size = [], 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'),
                               ensure_ascii=False, allow_nan=False)
    for chunk in encoder.iterencode(value):
        item = chunk.encode('utf-8')
        size += len(item)
        if size > _OUTPUT_LIMIT:
            raise _Abort('output', 'output_limit')
        chunks.append(item)
    return b''.join(chunks)


def _failure(stage: str, reason: str, evidence=None) -> dict:
    response = {'adapter_version': '1', 'status': 'rejected', 'synthetic': True,
                'stage': stage, 'reason': reason, 'details': [],
                'evidence': evidence, 'comparisons': []}
    try:
        _output_bytes(response)
        return response
    except (_Abort, TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        pass
    response['evidence'] = None
    return response


def _raw_fields(records: list):
    for record in records:
        if type(record) is not dict:
            continue  # Accepted factory reports invalid_record.
        if record.keys() - _RAW_KEYS:
            raise _Abort('input', 'invalid_input')
        for name, allowed in _RAW_NESTED.items():
            value = record.get(name)
            rows = value if name == 'evidence' and type(value) is list else [value]
            for row in rows:
                if type(row) is dict and row.keys() - allowed:
                    raise _Abort('input', 'invalid_input')


def _unique_rows(rows: list, key: str, fields: tuple[str, ...]) -> dict:
    found = {}
    for row in rows:
        if (type(row) is not dict or not all(type(row.get(name)) is str for name in fields)
                or not _id(row.get(key))):
            raise _Abort('join', 'invalid_join')
        identity = row[key]
        values = tuple(row[name] for name in fields)
        if identity in found and found[identity] != values:
            raise _Abort('join', 'inconsistent_join')
        found[identity] = values
    return found


def _graph_shape(graph: dict):
    specs = (
        ('nodes', frozenset(('id', 'kind')), ('id', 'kind'), (), ()),
        ('edges', frozenset(('from', 'to')), ('from', 'to'), (), ()),
        ('inventory', frozenset(('component_id', 'unit', 'starting_stock')),
         ('component_id', 'unit'), ('starting_stock',), ()),
        ('arrival_lines', frozenset(('line_id', 'shipment_id', 'purchase_order_id',
                                    'component_id', 'unit', 'quantity', 'arrival_at')),
         ('line_id', 'shipment_id', 'purchase_order_id', 'component_id', 'unit'),
         ('quantity',), ('arrival_at',)),
        ('commitments', frozenset(('commitment_id', 'production_order_id', 'required_at',
                                   'due_at', 'demands')),
         ('commitment_id', 'production_order_id'), (), ('required_at', 'due_at')),
    )
    try:
        for name, keys, ids, amounts, instants in specs:
            rows = graph[name]
            if type(rows) is not list:
                raise _Abort('input', 'invalid_input')
            for row in rows:
                if (type(row) is not dict or set(row) != keys or
                        not all(_id(row.get(field)) for field in ids)):
                    raise _Abort('input', 'invalid_input')
                if name == 'nodes' and row['kind'] not in {
                    'shipment', 'purchase_order', 'component', 'production_order', 'commitment'}:
                    raise _Abort('input', 'invalid_input')
                for field in amounts:
                    amount(row[field])
                for field in instants:
                    instant(row[field])
                if name == 'commitments':
                    demands = row['demands']
                    if type(demands) is not list:
                        raise _Abort('input', 'invalid_input')
                    for demand in demands:
                        if (type(demand) is not dict or set(demand) != {
                                'component_id', 'unit', 'quantity'} or
                                not _id(demand.get('component_id')) or
                                not _id(demand.get('unit'))):
                            raise _Abort('input', 'invalid_input')
                        amount(demand['quantity'])
    except ScreenError:
        raise _Abort('input', 'invalid_input') from None


def _join_inputs(request: dict, binding: dict):
    graph, production = request['propagation_snapshot'], request['production_snapshot']
    if (type(graph) is not dict or set(graph) - _GRAPH_KEYS or
            not set(_GRAPH_KEYS - {'notice'}).issubset(graph)):
        raise _Abort('input', 'invalid_input')
    if 'notice' in graph and type(graph['notice']) is not str:
        raise _Abort('input', 'invalid_input')
    try:
        _valid(production, _DEFINITIONS['ProductionSnapshot'])
        for row in request['recovery_assignments']:
            _valid(row, _DEFINITIONS['ProductionAssignment'])
    except (ValueError, TypeError, KeyError, RecursionError):
        raise _Abort('input', 'invalid_input') from None
    _graph_shape(graph)

    lines = _unique_rows(graph['arrival_lines'], 'line_id',
                         ('line_id', 'shipment_id', 'purchase_order_id', 'component_id',
                          'unit', 'quantity', 'arrival_at'))
    receipts = _unique_rows(production['receipts'], 'receipt_id',
                            ('receipt_id', 'component_id', 'unit', 'quantity', 'arrival_at'))
    graph_stock = _unique_rows(graph['inventory'], 'component_id',
                               ('component_id', 'unit', 'starting_stock'))
    production_stock = _unique_rows(production['inventory'], 'component_id',
                                    ('component_id', 'unit', 'starting_stock', 'stock_as_of'))
    joins = request['joins']
    if type(joins) is not list:
        raise _Abort('join', 'invalid_join')
    mapping, used_receipts = {}, set()
    for row in joins:
        if type(row) is not dict or set(row) != {'line_id', 'receipt_id'} or not all(_id(v) for v in row.values()):
            raise _Abort('join', 'invalid_join')
        line_id, receipt_id = row['line_id'], row['receipt_id']
        if line_id in mapping or receipt_id in used_receipts or line_id not in lines or receipt_id not in receipts:
            raise _Abort('join', 'invalid_join')
        mapping[line_id] = receipt_id
        used_receipts.add(receipt_id)
    if set(mapping) != set(lines):
        raise _Abort('join', 'invalid_join')

    try:
        cutoff = instant(binding['as_of'])
        for line_id, receipt_id in mapping.items():
            line, receipt = lines[line_id], receipts[receipt_id]
            component, unit, quantity, arrival = line[3], line[4], line[5], line[6]
            if ((component, unit) != (receipt[1], receipt[2]) or
                    amount(quantity) != amount(receipt[3]) or instant(arrival) != instant(receipt[4])):
                raise _Abort('join', 'inconsistent_join')
            if component not in graph_stock or component not in production_stock:
                raise _Abort('join', 'inconsistent_join')
            gs, ps = graph_stock[component], production_stock[component]
            if (gs[1] != unit or ps[1] != unit or amount(gs[2]) != amount(ps[2]) or
                    instant(ps[3]) != cutoff):
                raise _Abort('join', 'inconsistent_join')
    except ScreenError:
        raise _Abort('join', 'inconsistent_join') from None

    orders = {row['order_id'] for row in production['orders']}
    periods = {row['period_id'] for row in production['periods']}
    assignments = request['recovery_assignments']
    if len(assignments) != len(orders):
        raise _Abort('join', 'incomplete_assignments')
    seen = set()
    for row in assignments:
        if row['order_id'] not in orders or row['period_id'] not in periods or row['order_id'] in seen:
            raise _Abort('join', 'incomplete_assignments')
        seen.add(row['order_id'])
    return mapping, receipts


def _binding(request: dict, binding: dict):
    if type(binding) is not dict or set(binding) != _BINDING_KEYS or binding.get('adapter_version') != '1' or binding.get('synthetic') is not True:
        raise _Abort('binding', 'binding_mismatch')
    for name in ('workspace_id', 'plan_version', 'propagation_snapshot_id', 'production_snapshot_id'):
        if not _id(binding.get(name)):
            raise _Abort('binding', 'binding_mismatch')
    for name in ('raw_sha256', 'propagation_sha256', 'production_sha256', 'joins_sha256', 'assignments_sha256'):
        if type(binding.get(name)) is not str or not _HASH.fullmatch(binding[name]):
            raise _Abort('binding', 'binding_mismatch')
    graph, production = request['propagation_snapshot'], request['production_snapshot']
    try:
        if (type(graph) is not dict or type(production) is not dict or
            graph['workspace_id'] != binding['workspace_id'] or production['workspace_id'] != binding['workspace_id'] or
            graph['plan_version'] != binding['plan_version'] or production['plan_version'] != binding['plan_version'] or
            graph['snapshot_id'] != binding['propagation_snapshot_id'] or
            production['snapshot_id'] != binding['production_snapshot_id'] or
            graph['synthetic'] is not True or production['synthetic'] is not True or
            instant(graph['as_of']) != instant(binding['as_of']) or
            instant(production['as_of']) != instant(binding['as_of'])):
            raise _Abort('binding', 'binding_mismatch')
    except (KeyError, ScreenError):
        raise _Abort('binding', 'binding_mismatch') from None
    for name, value in (('raw_sha256', request['raw_records']),
                        ('propagation_sha256', graph), ('production_sha256', production),
                        ('joins_sha256', request['joins']),
                        ('assignments_sha256', request['recovery_assignments'])):
        if _hash(value) != binding[name]:
            raise _Abort('binding', 'binding_mismatch')


def _compose(request: dict, binding: dict) -> dict:
    if set(request) != _REQUEST_KEYS or request.get('adapter_version') != '1':
        raise _Abort('input', 'invalid_input')
    if any(type(request[name]) is not list for name in ('raw_records', 'joins', 'recovery_assignments')):
        raise _Abort('input', 'invalid_input')
    _raw_fields(request['raw_records'])
    _binding(request, binding)
    line_to_receipt, receipts = _join_inputs(request, binding)
    graph = request['propagation_snapshot']
    production = request['production_snapshot']
    try:
        source_binding = bind_synthetic_milestones(
            request['raw_records'], graph,
            expected_workspace_id=binding['workspace_id'],
            expected_plan_version=binding['plan_version'],
            expected_snapshot_id=binding['propagation_snapshot_id'])
        evidence = build_arrival_scenarios(source_binding, graph)
    except Exception:
        raise _Abort('source', 'source_unavailable') from None
    source_rows = evidence['scenarios']
    if len(source_rows) > 16:
        raise _Abort('input', 'input_limit', evidence)
    binding_sha = _hash(binding)
    bound = dict(binding, binding_sha256=binding_sha)
    response = {'adapter_version': '1', 'status': 'no_eligible_scenario',
                'synthetic': True, 'binding': bound, 'evidence': evidence,
                'comparisons': []}
    if not source_rows:
        return response

    # Complete every propagation projection before invoking any production math.
    projections = []
    for source in source_rows:
        try:
            projection = project_arrival_change(graph, source['scenario'])
        except Exception:
            raise _Abort('projection', 'unsupported_propagation', evidence) from None
        if type(projection) is not dict or projection.get('status') != 'screened':
            raise _Abort('projection', 'unsupported_propagation', evidence)
        projections.append(projection)

    comparisons = []
    for source, projection in zip(source_rows, projections):
        changed = {}
        try:
            for change in source['scenario']['changes']:
                line_id, arrival = change['line_id'], change['arrival_at']
                if line_id not in line_to_receipt:
                    raise _Abort('source', 'source_unavailable', evidence)
                receipt_id = line_to_receipt[line_id]
                if receipt_id in changed and instant(changed[receipt_id]) != instant(arrival):
                    raise _Abort('source', 'source_unavailable', evidence)
                changed[receipt_id] = arrival
            replacements = [
                {'receipt_id': receipt_id,
                 'expected_arrival_at': receipts[receipt_id][4],
                 'arrival_at': changed[receipt_id]}
                for receipt_id in sorted(changed)
            ]
            source_id = source['scenario']['scenario_id']
            scenario_id = 'shipment-recovery:' + _hash({
                'adapter_version': '1', 'source_scenario_id': source_id,
                'binding_sha256': binding_sha})
            scope = {'workspace_id': production['workspace_id'],
                     'expected_plan_version': production['plan_version'],
                     'source_snapshot_id': production['snapshot_id'],
                     'scenario_id': scenario_id, 'synthetic': True}
            receipt_scenario = dict(scope, arrival_replacements=replacements)
            recovery = dict(scope, assignments=request['recovery_assignments'])
        except _Abort:
            raise
        except Exception:
            raise _Abort('source', 'source_unavailable', evidence) from None
        try:
            result = compare_receipt_recovery(production, receipt_scenario, recovery)
        except Exception:
            raise _Abort('runtime', 'comparison_unavailable', evidence) from None
        if type(result) is not dict or result.get('status') not in ('compared', 'unsupported'):
            raise _Abort('output', 'invalid_result', evidence)
        try:
            schema = ('ReceiptRecoveryComparison' if result['status'] == 'compared'
                      else 'RecoveryComparisonUnsupportedResponse')
            wire_result = (result if result['status'] == 'compared' else
                           {'contract_version': '0.2.0', **result})
            _valid(wire_result, _DEFINITIONS[schema])
        except (ValueError, TypeError, KeyError, RecursionError):
            raise _Abort('output', 'invalid_result', evidence) from None
        comparisons.append({'shipment_id': source['shipment_id'],
                            'source_scenario_id': source_id,
                            'receipt_scenario': receipt_scenario,
                            'recovery': recovery, 'projection': projection,
                            'result': result})
    comparisons.sort(key=lambda row: (row['shipment_id'], row['source_scenario_id']))
    response['status'] = 'evaluated'
    response['comparisons'] = comparisons
    return response


def compare_shipment_production_recovery(request: dict, *, expected_binding: dict) -> dict:
    """Compose caller-bound synthetic source evidence and independent public math."""
    try:
        request_bytes = _bytes(request, _INPUT_LIMIT, 'input')
        binding_bytes = _bytes(expected_binding, _BINDING_LIMIT, 'binding')
        if type(request) is dict:
            _bytes(request.get('raw_records'), _PART_LIMIT, 'input')
            _bytes(request.get('propagation_snapshot'), _PART_LIMIT, 'input')
        owned_request = json.loads(request_bytes)
        owned_binding = json.loads(binding_bytes)
        if type(owned_request) is not dict:
            raise _Abort('input', 'invalid_input')
        response = _compose(owned_request, owned_binding)
        try:
            _output_bytes(response)
        except _Abort:
            raise _Abort('output', 'output_limit', response.get('evidence')) from None
        except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
            raise _Abort('output', 'invalid_result', response.get('evidence')) from None
        return response
    except _Abort as rejected:
        return _failure(rejected.stage, rejected.reason, rejected.evidence)
    except Exception:
        return _failure('runtime', 'comparison_unavailable')
