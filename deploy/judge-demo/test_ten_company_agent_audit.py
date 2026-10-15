"""Offline contract checks for the bounded hosted agent audit."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ten_company_agent_audit import (check_model_steps, scenario_and_messages,
                                     selected_profiles, Receipt, safe_path)
from smoke import AcceptanceError


def test_two_typed_messages_use_the_company_material_unit_and_default_quantity():
    profile = {'company_id': 'loomworks', 'materials': [
        {'material_id': 'cotton', 'name': 'Cotton', 'quantity_unit': 'bale'}],
        'quote_coverage': {'scenarios': [{'material_id': 'cotton',
            'fixture_ids': ['one', 'two', 'three'],
            'buyer_defaults': {'material': 'cotton', 'quantity': '1880',
                               'quantity_unit': 'bale'}}]}}
    scenario, material, messages = scenario_and_messages(profile)
    assert scenario['material_id'] == material['material_id'] == 'cotton'
    assert len(messages) == 2
    assert 'cotton' in messages[0].lower()
    assert 'quantity to 1880 bale' in messages[1]


def test_model_steps_require_three_real_roles_and_a_deterministic_verifier():
    result = {'agent_steps': [
        {'agent': role, 'model': 'gemini', 'provider_call': 'real'}
        for role in ('coordinator', 'evidence', 'procurement')] + [
        {'agent': 'verifier', 'model': 'deterministic', 'provider_call': 'none'}]}
    assert check_model_steps(result, 'stage') == 3
    result['agent_steps'][1]['provider_call'] = 'mocked'
    with pytest.raises(AcceptanceError):
        check_model_steps(result, 'stage')


def test_receipt_is_create_only_and_pending_request_survives_restart(tmp_path):
    path = tmp_path / 'audit.json'
    receipt = Receipt.create(path, {'ok': False, 'calls': []})
    receipt.before('company:fill', '/api/demo/operator', 'POST')
    with pytest.raises(FileExistsError):
        Receipt.create(path, {'ok': False, 'calls': []})
    assert 'csrf' not in path.read_text().lower()
    assert '"pending"' in path.read_text()


def test_generated_ids_are_redacted_from_request_paths():
    assert safe_path('/api/demo/offers/private-id/correct') == '/api/demo/offers/{id}/correct'
    assert safe_path('/api/demo/rfq/private-id/download') == '/api/demo/rfq/{id}/download'


def test_continuation_selects_only_later_companies_after_uncertain_request():
    profiles = [{'company_id': f'company-{i}'} for i in range(10)]
    assert [row['company_id'] for row in selected_profiles(profiles, 4)] == [
        f'company-{i}' for i in range(4, 10)]
