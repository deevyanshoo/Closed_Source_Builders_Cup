"""Thin judge integration: public fixtures, bounded projections, no numerical engine."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .judge_ai_receipts import summarize_gemini_receipts, _public_url

CONFIG = Path(__file__).resolve().parents[3] / 'config'
_PREPARE_RECOVERY = re.compile(
    r'^\s*(?:(?:please|kindly)\s+)?(?:(?:can|could|would)\s+you\s+)?'
    r'(?:prepare|create|draft)\s+(?:a\s+|the\s+)?(?:recovery|replacement)\b', re.I)
_PREVIEW_ONLY = re.compile(r'\b(?:what[- ]if|suppose|simulate|preview|assume|assuming)\b', re.I)
_NEGATED_ACTION = re.compile(r"\b(?:do\s+not|don.t|never|without)\s+(?:\w+\s+){0,3}?(?:prepare|create|draft)\b", re.I)
_DAY_NUMBER = (r'(?:\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|'
               r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|'
               r'twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)')
_RANGE_DURATION = re.compile(
    rf'\b{_DAY_NUMBER}(?:\s+(?:to|and|or)\s+|\s*[-–—]\s*){_DAY_NUMBER}\s*[- ]\s*(?:days?|weeks?)\b', re.I)
_ZERO_DURATION = re.compile(r'\b(?:0+|zero)\s*[- ]\s*(?:days?|weeks?)\b', re.I)


def _explicit_recovery_request(message):
    return (bool(_PREPARE_RECOVERY.search(message)) and not _PREVIEW_ONLY.search(message)
            and not _NEGATED_ACTION.search(message))


def _recovery_clarification(snapshot, missing, answer):
    return {'kind': 'needs_input', 'company_id': snapshot['company_id'],
            'context_version': snapshot['context_version'], 'missing_inputs': [missing],
            'answer': answer, 'evidence_refs': []}


def _ambiguous_recovery_duration(message):
    # The shared reader may extract a positive integer inside qualified or
    # decimal wording, or the final value of a range. Action promotion requires
    # one exact, strictly positive duration.
    return bool(_RANGE_DURATION.search(message) or _ZERO_DURATION.search(message)
        or re.search(
        r'\b(?:negative|minus|at least|about|around|up to|approximately|roughly|'
        r'more than|less than|under|over|between)\s+(?:\w+\s+){0,2}?'
        r'(?:\d+(?:[.,]\d+)?|a|one|two|three|four|seven|fourteen)\s*[- ]\s*'
        r'(?:days?|weeks?)\b', message, re.I)
        or re.search(r'(?<!\w)[+-]\s*\d+\s*[- ]\s*(?:days?|weeks?)\b', message, re.I)
        or re.search(r'\b\d+[.,]\d+\s*[- ]\s*(?:days?|weeks?)\b', message, re.I))


def _multiple_materials(snapshot, message):
    # Match the resolver's owned material IDs and names before its selected
    # material fallback can hide a second explicitly named material.
    matches = {row['material_id'] for row in snapshot['company'].get('materials', [])
               if re.search(r'(?<!\w)(?:' + re.escape(row['material_id']) + '|' +
                            re.escape(row['name']) + r')(?!\w)', message, re.I)}
    return len(matches) > 1

def fixture(name):
    return json.loads((CONFIG / name).read_text(encoding='utf-8'))

def completed_research(value):
    rows = []
    for record in list(value.research_records.values())[-12:]:
        result = record.get('result') or {}
        if (record.get('company_id') != value.company_id or
            record.get('context_version') != value.context_version or
            record.get('state') != 'completed' or result.get('status') != 'completed'):
            continue
        sources = [{'source_id': s['source_id'][:160], 'url': s['url'], 'title': str(s.get('title',''))[:220]}
                   for s in result.get('sources', [])[:8] if isinstance(s,dict)
                   and isinstance(s.get('source_id'),str) and _public_url(s.get('url'))]
        if sources:
            rows.append({'company_id': value.company_id, 'context_version': value.context_version,
                         'status': 'completed', 'checked_at': result.get('checked_at'),
                         'query': str(record.get('query',result.get('query','')))[:500],
                         'answer': str(result.get('answer',''))[:2000], 'sources': sources,
                         'evidence_refs': [s['source_id'] for s in sources]})
    return rows

def baseline_snapshot(snapshot):
    result = deepcopy(snapshot)
    result['plan'] = deepcopy(snapshot.get('last_plan') or snapshot.get('baseline_plan')
                              or (snapshot.get('plan') if not snapshot.get('recovery') else None))
    baseline = result['plan']
    if baseline:
        from .dispatch_view import build_dispatch_view
        result['shipments'] = build_dispatch_view(result['company'], result['offers'], baseline, None)
    return result

def resolve_local_turn(snapshot, message, *, now):
    from .judge_resolver import resolve_judge_turn
    if not isinstance(snapshot.get('context_version'),int):
        return None  # Legacy pure-reader snapshots have no session context.
    resolved = resolve_judge_turn(snapshot, message, now=now)
    if _explicit_recovery_request(message):
        if resolved and resolved.get('missing_inputs') == ['company']:
            return resolved
        if _ambiguous_recovery_duration(message):
            return _recovery_clarification(snapshot, 'delay_days',
                'Specify one exact, positive whole-day delay for the current owned shipment.')
        if _multiple_materials(snapshot, message):
            return _recovery_clarification(snapshot, 'material',
                'Choose one material and its current owned shipment or offer for recovery.')
        if snapshot.get('recovery') and snapshot.get('plan') != snapshot.get('last_plan'):
            return _recovery_clarification(snapshot, 'baseline_plan_id',
                'A recovery is already active. Specify the exact target shipment and baseline before preparing another recovery; the accepted state is unchanged.')
        # The resolver binds a single current shipment, selected offer, version,
        # material and exact duration. Reuse that binding for a review proposal.
        if resolved and resolved['kind'] == 'read_only_preview':
            request = resolved.get('preview_request') or {}
            if request.get('shipment_id') and request.get('baseline_plan_id'):
                return {**resolved, 'kind': 'recovery_proposal',
                        'recovery_parameters': {
                            'shipment_id': request['shipment_id'],
                            'delay_days_by_offer': request['delay_days_by_offer']},
                        'answer': 'Review the recovery comparison for the selected current shipment. No plan has changed.'}
        return resolved
    # The resolver decides hypothetical intent; only preview/input outcomes need
    # an original-baseline view. Factual reads keep the active recovery state.
    if resolved and resolved['kind'] in {'read_only_preview','needs_input','unavailable'}:
        resolved = resolve_judge_turn(baseline_snapshot(snapshot), message, now=now)
    # Explicit operational requests belong to the existing validated action path.
    if resolved and resolved.get('missing_inputs') == ['company']:
        return resolved
    if re.match(r'\s*(?:watch|navigate|open|draft|send|approve|apply|save|load|review)\b', message,re.I):
        return None
    # Source provenance and model-evidence explanation remain grounded reasoning;
    # a bare mention of news is not a request to search the public internet.
    if resolved and resolved['kind']=='research_proposal' and not re.search(
            r'\b(?:search|research|look up|latest|fresh|today|recent|newest|up.to.date|now)\b',message,re.I):
        return None
    # Pure public-only requests retain the exact validated question. Mixed turns
    # use the resolver's separated public subquestion so private facts stay local.
    if resolved and resolved['kind'] == 'research_proposal':
        from .research import prepare_research, ResearchValidationError
        try:
            exact = prepare_research(message, snapshot['company'], now)
            if exact:
                resolved['research_parameters'] = exact
        except ResearchValidationError:
            pass
    return resolved

def has_local_evidence(resolved, snapshot):
    if not resolved or resolved.get('kind') != 'internal_answer':
        return False
    private_refs = {row.get('offer_id') for row in snapshot.get('offers',[]) if isinstance(row,dict)}
    private_refs.update((snapshot.get(name) or {}).get('plan_id') for name in ('plan','last_plan','baseline_plan'))
    return any(isinstance(ref,str) and (ref.startswith(('profile:','workspace:')) or ref in private_refs)
               for ref in resolved.get('evidence_refs',[]))

def judge_outcome(resolved):
    kind = resolved['kind']
    research = resolved.get('follow_up_research') or resolved.get('research_parameters')
    candidate = ({'kind': 'recovery', 'parameters': deepcopy(resolved['recovery_parameters'])}
                 if kind == 'recovery_proposal' else
                 {'kind': 'research', 'parameters': deepcopy(research)} if research else None)
    tool = next((name for name in ('read_shipments','read_carbon') if any(name in ref for ref in resolved.get('evidence_refs',[]))), 'judge_read')
    result = {'status': 'proposal' if candidate else 'needs_clarification' if kind == 'needs_input' else 'completed',
            'assistant_text': resolved.get('answer') or resolved.get('outcome') or 'This comparison is unavailable.',
            'agent_steps': [{'agent': 'workspace', 'role': 'Grounded local evidence', 'status': 'completed',
                             'model': 'none', 'provider_call': 'none', 'tool': tool,
                             'summary': 'Read current owned records; no public search executed.'}],
            'evidence_ids': resolved.get('evidence_refs', [])[:16], 'candidate': candidate,
            'judge_resolution': resolved, 'utility': None,
            'proposal_kind': candidate['kind'] if candidate else None,
            'parameters': deepcopy(candidate['parameters']) if candidate else None}
    if resolved.get('analytics'):
        result['analytics'] = deepcopy(resolved['analytics'])
    return result

def evidence(value):
    aggregate = summarize_gemini_receipts(value.judge_receipts.get(value.company_id, []))
    case = None
    if value.company_id == 'loomworks':
        source = fixture('judge_hindi_case_v1.json')
        match = next((r for r in aggregate['receipts'] if r['input_source_id'] == source['source_id']),None)
        case = {'status': 'success' if match else 'unavailable', 'inputSourceId': source['source_id'],
                'originalTitle': source['original_title'], 'publisher': source['publisher'],
                'publishedAt': source['publication_at'], 'sourceUrl': source['source_url']}
        if match:
            case.update(translationSummary=match['output'], nextAction=source['exposure_limit'])
    from .judge_replay import replay_at
    replay = fixture('judge_suez_2021_v1.json')
    replay['timeline'] = [replay_at(replay,datetime(2021,3,day,23,59,59,999999,tzinfo=timezone.utc))
                          for day in range(23,31)]
    return {'company_id': value.company_id, 'context_version': value.context_version,
            'aggregate': aggregate, 'hindi_case': case, 'replay': replay}

def hindi_payload():
    source = fixture('judge_hindi_case_v1.json')
    return {'source_id': source['source_id'], 'title': source['original_title'],
            'source_url': source['source_url'], 'publication_at': source['publication_at'],
            'instruction': 'Interpret only this curated Hindi title. Return exactly one JSON object with an "output" string and a "model_reviewed_flags" array. In output, give a short English rendering, conditional regional cotton sourcing implications, and the limits of what the title establishes. Example shape only: {"output":"English rendering: <your rendering>. Conditional implication: <your cautious implication>. Limitation: <what the title cannot establish>.","model_reviewed_flags":["review_source"]}. Replace the example placeholders with your own interpretation. Choose at most three applicable flags from public_policy_change, regional_import_exposure, review_source. Do not infer supplier price, stock or Bangladesh duty. No tools or URL fetch.'}

def sanitize_receipt(raw, now):
    if not isinstance(raw,dict):
        return None
    source = fixture('judge_hindi_case_v1.json')
    def clean(v,n):
        return re.sub(r'[\x00-\x1f\x7f]', ' ',v).strip()[:n] if isinstance(v,str) else ''
    model, version = clean(raw.get('model_id'),120), clean(raw.get('model_version'),120)
    output = clean(raw.get('output'),1500)
    if not model.startswith('gemini-') or not version or not output:
        return None
    record = {'provider': 'vertex_ai', 'status': 'success', 'model_id': model, 'model_version': version,
              'provider_call_id': clean(raw.get('provider_call_id'),120) or None,
              'input_source_id': source['source_id'], 'called_at': now.isoformat(),
              'source_title': source['original_title'], 'source_url': source['source_url'],
              'source_as_of': source['publication_at'][:10], 'output': output,
              'evidence_refs': [source['source_id'],source['corroboration_url']],
              'model_reviewed_flags': sorted({f for f in raw.get('model_reviewed_flags',[]) if isinstance(f,str)} &
                                             {'regional_import_exposure','public_policy_change','review_source'})}
    return record if summarize_gemini_receipts([record])['interpreted_articles'] == 1 else None

_HINDI_RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'output': {'type': 'string'},
        'model_reviewed_flags': {'type': 'array', 'maxItems': 3,
                                 'items': {'type': 'string', 'enum': [
                                     'public_policy_change', 'regional_import_exposure', 'review_source']}},
    },
    'required': ['output', 'model_reviewed_flags'],
}


class VertexHindiProvider:
    """Same lazy, offline-aware Vertex boundary as the workspace operator."""
    def __call__(self,payload):
        from .operator import VertexOperatorProvider, OperatorUnavailable
        from google import genai
        from google.genai import types
        import os
        boundary = VertexOperatorProvider()
        if not boundary.available:
            raise OperatorUnavailable('Vertex unavailable')
        client = genai.Client(vertexai=True, project=os.environ['GOOGLE_CLOUD_PROJECT'].strip(),
                              location=os.environ['GOOGLE_CLOUD_LOCATION'].strip(),
                              http_options=types.HttpOptions(api_version='v1',timeout=15000))
        try:
            response = client.models.generate_content(model=boundary.model, contents=json.dumps(payload,ensure_ascii=False),
                config=types.GenerateContentConfig(temperature=0,max_output_tokens=640,response_mime_type='application/json',
                    response_json_schema=_HINDI_RESPONSE_SCHEMA,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
            candidates = response.candidates or []
            if len(candidates)!=1 or str(candidates[0].finish_reason).split('.')[-1].upper()!='STOP':
                raise OperatorUnavailable('Incomplete interpretation')
            raw = response.text
            if not raw or len(raw)>6000:
                raise OperatorUnavailable('Invalid interpretation')
            result = json.loads(raw)
            result.update(model_id=boundary.model, model_version=getattr(response,'model_version',None),
                          provider_call_id=getattr(response,'response_id',None))
            return result
        finally:
            client.close()
