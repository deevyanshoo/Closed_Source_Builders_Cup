"""Carbon component boundaries, mass basis and catalogue coverage (Divyanshu's review, 16 Oct)."""
import pytest

from procure.carbon import CarbonInputError, compare_plan, coverage_state, estimate, load_registry
from procure.demo.company import get_company, list_companies

SEA = [{'mode': 'sea_container', 'distance_km': '1000'}]


@pytest.fixture(scope='module')
def registry():
    return load_registry()


def test_overlapping_point_of_sale_transport_has_no_combined_total(registry):
    result = estimate(registry=registry, material_id='pet-resin', quantity='8000', unit='kg', legs=SEA,
                      company_id='terrapack')
    assert result['material']['kg_co2e'] == '30892.66008'  # 8 t x 3861.58251, a proxy component
    assert result['transport']['kg_co2e'] == '128.96'      # 8 t x 1000 km x 0.01612
    assert result['total_kg_co2e'] is None
    assert result['combined_status'] == 'components_shown_separately'
    assert 'transport to the point of sale' in result['combined_reason']


def test_pet_including_forming_is_not_exact_resin(registry):
    factor = registry['materials']['pet-resin']
    assert factor['product_match'] == 'proxy'
    assert 'forming' in factor['note'] and 'not an exact resin factor' in factor['note'].lower()
    result = estimate(registry=registry, material_id='pet-resin', quantity='1000', unit='kg', legs=[],
                      company_id='terrapack')
    assert result['material']['product_match'] == 'proxy'


@pytest.mark.parametrize('legs', [[], [{'mode': 'pipeline', 'distance_km': '10'}], [{'mode': 'sea_container'}]])
def test_partial_or_missing_transport_has_no_whole_total(registry, legs):
    result = estimate(registry=registry, material_id='copper', quantity='8000', unit='kg', legs=legs,
                      company_id='solara-cables')
    assert result['material']['kg_co2e'] == '31720'
    assert result['total_kg_co2e'] is None
    assert result['combined_status'] == 'incomplete'


def test_non_mass_quantity_requires_explicit_mass_basis(registry):
    missing = estimate(registry=registry, material_id='vials', quantity='100000', unit='unit', legs=SEA,
                       company_id='meridian-pharma')
    assert missing['material']['status'] == 'unit_mismatch'
    assert missing['transport']['legs'][0]['status'] == 'mass_basis_required'
    entered = estimate(registry=registry, material_id='vials', quantity='100000', unit='unit', legs=SEA,
                       company_id='meridian-pharma', mass_override_kg='2500', mass_basis='Supplier packing list, 25 g per vial')
    # Shipping mass is used for freight only: 2.5 t x 1000 km x 0.01612 = 40.3
    assert entered['transport']['kg_co2e'] == '40.3'
    assert entered['mass_basis'] == {'mass_kg': '2500', 'basis': 'Supplier packing list, 25 g per vial',
                                     'source': 'entered by you', 'used_for': 'freight only'}
    assert entered['material']['status'] == 'unit_mismatch'
    assert entered['total_kg_co2e'] is None
    for bad in ('0', '-1', 'heavy', 2500):
        with pytest.raises(CarbonInputError):
            estimate(registry=registry, material_id='vials', quantity='1', unit='unit', legs=SEA,
                     mass_override_kg=bad, mass_basis='x')
    with pytest.raises(CarbonInputError):
        estimate(registry=registry, material_id='vials', quantity='1', unit='unit', legs=SEA, mass_override_kg='10')


def test_each_catalogue_material_has_an_explicit_coverage_state(registry):
    lines = [(c['company_id'], m) for s in list_companies() for c in [get_company(s['company_id'])] for m in c['materials']]
    assert len(lines) == 52
    states = {}
    for company_id, material in lines:
        state = coverage_state(registry, company_id, material['material_id'], material['quantity_unit'])
        assert state['state'] in {'exact', 'proxy', 'unit_mismatch', 'unavailable'}
        if state['state'] != 'exact':
            assert state['reason'], (company_id, material['material_id'])
        states[(company_id, material['material_id'])] = state['state']
    assert states[('solara-cables', 'copper')] == 'exact'
    assert states[('forgeline-components', 'copper')] == 'proxy'
    assert states[('meridian-pharma', 'vials')] == 'unit_mismatch'
    assert states[('solara-cables', 'polymer')] == 'unavailable'
    assert sum(1 for s in states.values() if s == 'exact') == 2


def test_every_catalogue_line_resolves_to_a_state(registry):
    catalogue = {(e['company_id'], e['material_id']) for e in registry['catalogue']}
    company_lines = {(s['company_id'], m['material_id']) for s in list_companies() for m in get_company(s['company_id'])['materials']}
    assert catalogue == company_lines


def test_zero_distance_is_distinct_from_missing_distance(registry):
    zero = estimate(registry=registry, material_id='copper', quantity='8000', unit='kg',
                    legs=[{'mode': 'road_hgv', 'distance_km': '0'}], company_id='solara-cables')
    missing = estimate(registry=registry, material_id='copper', quantity='8000', unit='kg',
                       legs=[{'mode': 'road_hgv'}], company_id='solara-cables')
    assert zero['transport']['legs'][0]['status'] == 'zero_distance'
    assert zero['transport']['legs'][0]['kg_co2e'] == '0'
    assert missing['transport']['legs'][0]['status'] == 'distance_missing'
    assert missing['transport']['legs'][0]['kg_co2e'] is None


def test_copper_cathode_arithmetic_and_exact_combination(registry):
    result = estimate(registry=registry, material_id='copper', quantity='8000', unit='kg', legs=SEA,
                      company_id='solara-cables')
    # 8 t x 3965 = 31720 (ICA, ends at the refinery gate) + 8 t x 1000 km x 0.01612 = 128.96
    assert result['combined_status'] == 'combined'
    assert result['total_kg_co2e'] == '31848.96'
    rod = estimate(registry=registry, material_id='copper', quantity='8000', unit='kg', legs=SEA,
                   company_id='forgeline-components')
    assert rod['material']['product_match'] == 'proxy' and rod['total_kg_co2e'] is None


def test_steel_arithmetic_uses_the_labelled_desnz_proxy(registry):
    result = estimate(registry=registry, material_id='steel', quantity='700', unit='tonne', legs=[],
                      company_id='forgeline-components')
    assert result['material']['kg_co2e'] == '2003107.757'  # 700 t x 2861.58251
    assert result['material']['product_match'] == 'proxy'
    assert result['material']['factor']['category'] == 'Metal: steel cans'


def test_aluminium_arithmetic_uses_the_labelled_desnz_proxy(registry):
    result = estimate(registry=registry, material_id='aluminium', quantity='2500', unit='kg', legs=SEA,
                      company_id='solara-cables')
    assert result['material']['kg_co2e'] == '22783.956275'  # 2.5 t x 9113.58251
    assert result['transport']['kg_co2e'] == '40.3'
    assert result['total_kg_co2e'] is None  # DESNZ factor includes transport to point of sale


def test_compare_plan_passes_mass_basis_and_company(registry):
    plan = {'plan_id': 'p1', 'material': 'vials', 'quantity_unit': 'unit',
            'alternatives': [{'offer_id': 'q1', 'supplier': 'A', 'quantity': '1000'}]}
    result = compare_plan(registry, plan, {'q1': SEA}, {'q1': {'mass_kg': '25', 'basis': 'stated'}},
                          company_id='meridian-pharma')
    assert result['suppliers'][0]['estimate']['transport']['kg_co2e'] == '0.403'
    with pytest.raises(CarbonInputError):
        compare_plan(registry, plan, {}, {'other': {'mass_kg': '1', 'basis': 'x'}}, company_id='meridian-pharma')
