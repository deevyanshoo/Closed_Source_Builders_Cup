"""Offline acceptance logic checks; no hosted or model calls."""
import asyncio
import base64
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'procurement-engine' / 'src'))

import broad_inventory_operator_audit as audit
from procure.demo.company import get_company
from procure.demo.agent_tools import inventory_analytics
from procure.demo.operator import _broad_inventory_answer
from smoke import AcceptanceError


def profile():
    return get_company('solara-cables')


def result(answer):
    return {'company_id': 'solara-cables', 'context_version': 7,
            'status': 'completed', 'read_only': True, 'proposal': None,
            'assistant_text': answer}


def test_primary_answer_requires_profile_facts_and_rejects_unknown_number():
    answer = ('For Solara Cables, Copper cathode stock is 2,000 kg with daily use '
              'of 333.33 kg and about 6 days of stock cover. Europa Copper SA '
              'has a listed 10 day supplier lead time, a 4 day lead minus cover '
              'gap. Pricing is unavailable because no reviewed quotes are loaded; '
              'these are current profile facts.')
    checked = audit.check_answer(result(answer), profile(), 7, 'primary')
    assert checked['numbers_checked'] == 5
    with pytest.raises(AcceptanceError, match='unverified_number'):
        audit.check_answer(result(answer.replace('4 day lead', '999999 day lead')),
                           profile(), 7, 'primary')
    with pytest.raises(AcceptanceError, match='stock_cover_missing'):
        audit.check_answer(result(answer.replace('6 days of stock cover',
                                                 '10 days of stock cover')),
                           profile(), 7, 'primary')
    with pytest.raises(AcceptanceError, match='unreviewed_price_claim'):
        audit.check_answer(result(answer + ' Price is 2,000 USD.'),
                           profile(), 7, 'primary')
    with pytest.raises(AcceptanceError, match='not_useful_read_only_answer'):
        audit.check_answer({**result(answer), 'proposal': {'kind': 'rfq'}}, profile(), 7, 'primary')


def test_companywide_answer_requires_multiple_materials():
    answer = ('At Solara Cables, Copper cathode stock is 2,000 kg; its sourcing '
              'options include Europa Copper SA at 10 days. Cable insulation '
              'polymer has 5,800 kg of stock, so compare stock cover and supplier '
              'lead times. These are fictional profile facts; no reviewed quotes are loaded.')
    assert len(audit.check_answer(result(answer), profile(), 7, 'companywide')['materials_named']) == 2
    with pytest.raises(AcceptanceError, match='companywide_facts_missing'):
        audit.check_answer(result(answer.replace('Cable insulation polymer', 'Another input')),
                           profile(), 7, 'companywide')


def test_voice_analytics_must_match_profile_and_primary_material():
    company = profile()
    analytics = inventory_analytics({'company': company})
    voice = {**result('Current workspace facts are ready.'), 'analytics': analytics}
    assert audit.check_analytics(voice, company, 7) == [m['material_id'] for m in company['materials']]
    analytics['materials'][0]['stock'] = '9999'
    with pytest.raises(AcceptanceError, match='voice_profile_fact_mismatch'):
        audit.check_analytics(voice, company, 7)


def test_company_subset_has_no_duplicate_or_unknown():
    profiles = [profile()] * 10
    with pytest.raises(AcceptanceError, match='invalid_catalogue'):
        audit.selected(profiles, ['solara-cables'])


def test_voice_turn_waits_for_grounded_tool_result_and_output_audio():
    company = profile()
    analytics = inventory_analytics({'company': company})
    events = [
        {'type': 'transcript', 'direction': 'input',
         'text': 'Explain inventory position and sourcing options for this company'},
        {'type': 'turn_complete'},
        {'type': 'tool_result', 'result': {**result('Current workspace facts are ready.'),
                                          'analytics': analytics}},
        {'type': 'transcript', 'direction': 'output',
         'text': 'Copper cathode has 2,000 kg in stock and Europa Copper has a 10 day lead.'},
        {'type': 'audio', 'sample_rate_hertz': 24000,
         'data': base64.b64encode(b'\0' * 10000).decode('ascii')},
        {'type': 'turn_complete'},
    ]

    class Socket:
        async def send(self, payload):
            assert json.loads(payload)['type'] in {'audio', 'audio_end'}

        async def recv(self):
            return json.dumps(events.pop(0))

    output = asyncio.run(audit.voice_turn(Socket(), b'\0' * 3200, company, 7))
    assert output['output_audio_bytes'] == 10000
    assert output['input_transcript_verified'] is True
    assert 'Europa Copper' in output['output_transcript']
    assert output['output_caption_grounding_verified'] is True
    assert output['output_audio_semantics_verified'] is False

    events = [
        {'type': 'transcript', 'direction': 'input',
         'text': 'Explain inventory position and sourcing options for this company'},
        {'type': 'tool_result', 'result': {**result('Current workspace facts are ready.'),
                                          'analytics': analytics}},
        {'type': 'transcript', 'direction': 'output',
         'text': 'Unrelated material has 999999 units and I have placed an order.'},
        {'type': 'audio', 'sample_rate_hertz': 24000,
         'data': base64.b64encode(b'\0' * 10000).decode('ascii')},
        {'type': 'turn_complete'},
    ]
    with pytest.raises(AcceptanceError, match='voice_caption_not_grounded'):
        asyncio.run(audit.voice_turn(Socket(), b'\0' * 3200, company, 7))


def test_failed_text_checker_preserves_observed_answer(monkeypatch):
    company = profile()
    answer = 'Solara Cables has stock, but I need to look up sourcing.'
    calls = []

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def json(self, _stage, path, **_kwargs):
            calls.append(path)
            if path == '/api/demo/session':
                return {'company_id': company['company_id'], 'context_version': 7,
                        'csrf_token': 'fake', 'synthetic': True, 'offers': [],
                        'drafts': [], 'simulated_outbox': []}
            return result(answer)

        def close(self):
            pass

    monkeypatch.setattr(audit, 'PooledClient', Client)
    row = {'company_id': company['company_id'], 'typed': {}}
    snapshots = []
    def save():
        snapshots.append(json.loads(json.dumps(row)))
    with pytest.raises(AcceptanceError):
        audit.visit('https://example.test', company, None, row, save)
    assert calls == ['/api/demo/session', '/api/demo/operator']
    assert row['typed']['primary']['assistant_text'] == answer
    assert row['typed']['primary']['status'] == 'completed'
    assert snapshots[-1]['typed']['primary']['assistant_text'] == answer


@pytest.mark.parametrize('caption', [
    ('Copper cathode has six days of cover and supplier sourcing options. '
     'The price is fifty dollars per kilogram.'),
    ('Copper cathode has six days of cover and supplier sourcing options. '
     'We will place an order now.'),
])
def test_voice_caption_rejects_spoken_price_and_future_action(caption):
    with pytest.raises(AcceptanceError, match='voice_caption_not_grounded'):
        audit.check_voice_caption(caption, profile(), ['copper'])


def test_r65_valid_caption_phrasing_and_invalid_under_threshold():
    terra = get_company('terrapack')
    caption = ('PET resin requires attention first, as it has the lowest stock '
               'cover at just under 20 days. ColorMill offers the shortest lead '
               'time of 6 days for packaging inks.')
    checked = audit.check_voice_caption(caption, terra,
                                        [m['material_id'] for m in terra['materials']])
    assert checked['material_id'] == 'pet-resin'
    with pytest.raises(AcceptanceError, match='voice_caption_bad_cover_threshold'):
        audit.check_voice_caption(caption.replace('under 20 days', 'under 10 days'),
                                  terra, [m['material_id'] for m in terra['materials']])

    forge = get_company('forgeline-components')
    caption = ('Cold-rolled steel requires attention first, as it has the lowest '
               'stock cover at approximately 25 days. The suppliers with the shortest '
               'lead times are the Alternate Source options for each material, all '
               'offering a 10-day lead time.')
    checked = audit.check_voice_caption(caption, forge,
                                        [m['material_id'] for m in forge['materials']])
    assert checked['material_id'] == 'steel'


def test_canonical_voice_read_is_grounded_without_analytics():
    company = get_company('harvest-foods')
    answer = _broad_inventory_answer({'company': company,
                                      'company_id': company['company_id']},
                                     'company-wide', 'Give an inventory and sourcing overview')
    canonical = {'company_id': company['company_id'], 'context_version': 7,
                 'status': 'completed', 'read_only': True, 'proposal': None,
                 'assistant_text': answer,
                 'agent_steps': [{'tool': 'inventory_sourcing_read',
                                  'provider_call': 'none', 'status': 'completed'}]}
    ids, path = audit.check_voice_tool_result(canonical, company, 7)
    assert path == 'canonical_inventory_sourcing_read'
    assert len(ids) >= 2
    with pytest.raises(AcceptanceError, match='voice_unsupported_tool_result'):
        audit.check_voice_tool_result({**canonical, 'agent_steps': [
            {'tool': 'prepare_rfq', 'provider_call': 'none', 'status': 'completed'}]},
            company, 7)
    with pytest.raises(AcceptanceError, match='not_useful_read_only_answer'):
        audit.check_voice_tool_result({**canonical, 'action_result': {'tool': 'fill_form'}},
                                      company, 7)


def test_invalid_voice_tool_observation_is_checkpointed_without_secrets():
    company = get_company('harvest-foods')
    event = {'type': 'tool_result', 'result': {
        'company_id': company['company_id'], 'context_version': 7,
        'status': 'completed', 'assistant_text': 'Current company facts are ready.',
        'agent_steps': [{'tool': 'prepare_rfq', 'provider_call': 'none'}],
        'csrf_token': 'secret-token'}}
    class Socket:
        async def send(self, _payload):
            pass

        async def recv(self):
            return json.dumps(event)

    trace, checkpoints = {}, []
    with pytest.raises(AcceptanceError, match='voice_unsupported_tool_result'):
        asyncio.run(audit.voice_turn(Socket(), b'\0' * 3200, company, 7,
                                     trace, lambda: checkpoints.append(json.loads(json.dumps(trace)))))
    assert checkpoints[-1]['tool_observation']['agent_step_tools'] == ['prepare_rfq']
    assert 'secret-token' not in json.dumps(checkpoints)


def test_owned_risk_read_is_intermediate_before_grounded_inventory_answer():
    company = get_company('stonebridge-materials')
    material = company['materials'][0]
    suppliers = [s for s in company['suppliers'] if material['material_id'] in s['materials']]
    fastest = min(suppliers, key=lambda s: s['lead_time_days'])
    caption = (f"{material['name']} has about {round(audit.expected_cover(material))} days "
               f"of stock cover. {fastest['name'].replace(' [SYNTHETIC]', '')} "
               f"has a {fastest['lead_time_days']}-day supplier lead time.")
    company_risk = {'company_id': company['company_id'], 'company_name': company['name'],
                    'alerts': [{'material_ids': [material['material_id']]}]}
    risk = {'company_id': company['company_id'], 'context_version': 7,
            'status': 'completed', 'read_only': True, 'proposal': None,
            'assistant_text': 'Scanned 1 company for risk signals.',
            'agent_steps': [{'tool': 'read_risks', 'provider_call': 'none',
                             'status': 'completed'}],
            'workspace': {'risks': {'companies': [company_risk]}}}
    analytics = {'company_id': company['company_id'], 'context_version': 7,
                 'status': 'completed', 'read_only': True, 'proposal': None,
                 'analytics': inventory_analytics({'company': company})}
    audio = {'type': 'audio', 'sample_rate_hertz': 24000,
             'data': base64.b64encode(b'\0' * 10000).decode('ascii')}
    events = [
        {'type': 'transcript', 'direction': 'input',
         'text': 'Explain inventory and supplier sourcing options for this company'},
        {'type': 'tool_result', 'result': risk},
        {'type': 'transcript', 'direction': 'output', 'text': 'Scanned one company.'},
        {'type': 'turn_complete'},
        {'type': 'tool_result', 'result': analytics},
        {'type': 'transcript', 'direction': 'output', 'text': caption},
        audio, {'type': 'turn_complete'},
    ]

    class Socket:
        async def send(self, _payload):
            pass

        async def recv(self):
            return json.dumps(events.pop(0))

    trace = {}
    output = asyncio.run(audit.voice_turn(Socket(), b'\0' * 3200, company, 7,
                                          trace, lambda: None))
    assert output['tool_validation_path'] == 'profile_analytics'
    assert output['output_transcript'] == caption
    assert trace['owned_risk_intermediates'] == 1
    assert [obs['agent_step_tools'] for obs in trace['tool_observations']] == [
        ['read_risks'], []]

    for invalid in (
        {**risk, 'workspace': {'risks': {'companies': [
            risk['workspace']['risks']['companies'][0], {'company_id': 'other'}]}}},
        {**risk, 'workspace': {'risks': {'companies': [{
            **risk['workspace']['risks']['companies'][0],
            'alerts': [{'material_ids': ['foreign-material']}]}]}}},
    ):
        with pytest.raises(AcceptanceError):
            audit.check_voice_tool_result(invalid, company, 7)

    events = [{'type': 'tool_result', 'result': risk},
              {'type': 'turn_complete'}, {'type': 'error', 'code': 'end_without_inventory'}]
    with pytest.raises(AcceptanceError, match='voice_error'):
        asyncio.run(audit.voice_turn(Socket(), b'\0' * 3200, company, 7))
