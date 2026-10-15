from copy import deepcopy
from decimal import Decimal

import pytest

from procure.carbon import CarbonInputError, compare_plan, estimate, load_registry


@pytest.fixture(scope='module')
def registry():
    return load_registry()


def test_registry_cites_source_url_licence_year_and_unit_for_every_factor(registry):
    assert registry['label'] == 'estimate, not a certified footprint'
    for group in ('transport_modes', 'materials'):
        for key, factor in registry[group].items():
            source = registry['sources'][factor['source']]
            assert source['page_url'].startswith('https://www.gov.uk/'), key
            assert source['licence'] == 'Open Government Licence v3.0'
            assert source['factor_year'] == 2026
            assert factor['unit'].startswith('kg CO2e per ')
            assert Decimal(factor['factor']) > 0


def test_registry_rejects_a_factor_without_a_cited_source(registry, tmp_path):
    broken = deepcopy(registry)
    broken['materials']['pet-resin']['source'] = 'unknown-source'
    path = tmp_path / 'carbon.json'
    import json
    path.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match='pet-resin'):
        load_registry(path)


def test_hand_calculated_material_and_transport(registry):
    result = estimate(registry=registry, material_id='pet-resin', quantity='8000', unit='kg',
                      legs=[{'mode': 'sea_container', 'distance_km': '1000'},
                            {'mode': 'road_hgv', 'distance_km': '250'}])
    # 8 t x 3861.58251 = 30892.66008
    assert result['material']['status'] == 'estimated'
    assert result['material']['mass_tonnes'] == '8'
    assert result['material']['kg_co2e'] == '30892.66008'
    # 8 t x 1000 km x 0.01612 = 128.96; 8 t x 250 km x 0.10356 = 207.12
    legs = result['transport']['legs']
    assert [leg['tonne_km'] for leg in legs] == ['8000', '2000']
    assert [leg['kg_co2e'] for leg in legs] == ['128.96', '207.12']
    assert result['transport']['kg_co2e'] == '336.08'
    assert result['total_kg_co2e'] == '31228.74008'
    assert result['complete'] is True
    assert result['label'] == 'estimate, not a certified footprint'
    assert 'Cradle-to-gate' in result['scope']
    assert result['factor_versions'] == {'carbon-factors-v1': {'desnz-2026-full-set': {
        'published_on': '2026-06-11', 'checked_on': '2026-10-15', 'factor_year': 2026,
        'file_sha256': registry['sources']['desnz-2026-full-set']['file_sha256']}}}


def test_tonne_quantities_are_used_as_mass_directly(registry):
    result = estimate(registry=registry, material_id='aggregate', quantity='2.5', unit='tonne', legs=[])
    assert result['material']['kg_co2e'] == '19.507675'
    assert result['transport']['status'] == 'not_entered'
    assert result['complete'] is False


def test_unknown_material_and_mode_are_factor_unavailable_never_guessed(registry):
    result = estimate(registry=registry, material_id='copper', quantity='1000', unit='kg',
                      legs=[{'mode': 'pipeline', 'distance_km': '10'}])
    assert result['material'] == {'status': 'factor_unavailable', 'kg_co2e': None, 'factor': None,
                                  'mass_tonnes': '1', 'reason': 'No published factor is registered for copper.'}
    leg = result['transport']['legs'][0]
    assert leg['status'] == 'factor_unavailable' and leg['kg_co2e'] is None
    assert result['total_kg_co2e'] is None
    assert result['complete'] is False


def test_count_units_are_a_unit_mismatch_for_mass_factors(registry):
    result = estimate(registry=registry, material_id='vials', quantity='100000', unit='unit',
                      legs=[{'mode': 'road_hgv', 'distance_km': '500'}])
    assert result['material']['status'] == 'unit_mismatch'
    assert result['material']['kg_co2e'] is None
    assert 'unit' in result['material']['reason']
    assert result['transport']['legs'][0]['status'] == 'unit_mismatch'
    assert result['total_kg_co2e'] is None


def test_zero_quantity_gives_zero_not_unavailable(registry):
    result = estimate(registry=registry, material_id='pet-resin', quantity='0', unit='kg',
                      legs=[{'mode': 'rail_freight', 'distance_km': '300'}])
    assert result['material']['kg_co2e'] == '0'
    assert result['transport']['kg_co2e'] == '0'
    assert result['total_kg_co2e'] == '0'


@pytest.mark.parametrize('bad', [{'mode': 'road_hgv'}, {'mode': 'road_hgv', 'distance_km': '-1'},
                                 {'mode': 'road_hgv', 'distance_km': 12.5}, {'mode': 'road_hgv', 'distance_km': 'far'},
                                 {'mode': 'road_hgv', 'distance_km': '10', 'extra': 1}])
def test_distances_must_be_explicit_non_negative_decimal_text(registry, bad):
    with pytest.raises(CarbonInputError):
        estimate(registry=registry, material_id='pet-resin', quantity='1', unit='kg', legs=[bad])


def test_compare_plan_adds_one_estimate_per_supplier_with_versions(registry):
    plan = {'plan_id': 'p1', 'material': 'pet-resin', 'quantity_unit': 'kg',
            'alternatives': [{'offer_id': 'q1', 'supplier': 'A [SYNTHETIC]', 'quantity': '8000'},
                             {'offer_id': 'q2', 'supplier': 'B [SYNTHETIC]', 'quantity': '8000'}]}
    result = compare_plan(registry, plan, {'q1': [{'mode': 'sea_container', 'distance_km': '1000'}]})
    assert result['plan_id'] == 'p1'
    assert result['synthetic_inputs'] is True
    assert result['registry_version'] == 'carbon-factors-v1'
    assert [row['offer_id'] for row in result['suppliers']] == ['q1', 'q2']
    assert result['suppliers'][0]['estimate']['total_kg_co2e'] == '31021.62008'
    assert result['suppliers'][1]['estimate']['transport']['status'] == 'not_entered'
    with pytest.raises(CarbonInputError):
        compare_plan(registry, plan, {'q9': []})
