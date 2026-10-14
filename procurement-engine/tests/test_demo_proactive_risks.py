"""Pure proactive risk assessment over fictional companies and observed news metadata."""
from copy import deepcopy
from datetime import datetime, timezone

from procure.demo.company import list_companies, get_company
from procure.demo.proactive_risks import assess_company
from procure.demo import planner, quote_service, recovery


NOW = datetime(2026, 10, 14, 10, tzinfo=timezone.utc)


def snapshot(company_id='solara-cables'):
    return {'company_id': company_id, 'company': get_company(company_id),
            'offers': [], 'last_plan': None, 'plan': None, 'plan_request': None,
            'recovery': None, 'buyer_draft': None, 'selected_material_id': None}


def test_all_ten_companies_evaluate_all_52_materials_without_mutation_or_news():
    evaluated = 0
    for company in list_companies():
        original = snapshot(company['company_id'])
        untouched = deepcopy(original)
        result = assess_company(original, None, NOW)
        assert original == untouched
        assert set(result) == {'company_id', 'company_name', 'sector', 'checked_at',
                               'alerts', 'coverage', 'data_gaps'}
        assert result['company_id'] == company['company_id']
        assert result['coverage']['materials_evaluated'] == len(company['materials'])
        assert result['coverage']['news_state'] == 'unavailable'
        assert len(result['alerts']) <= 60
        assert all(alert['company_id'] == company['company_id'] for alert in result['alerts'])
        assert all(alert['provenance'].startswith('synthetic_') for alert in result['alerts'])
        evaluated += result['coverage']['materials_evaluated']
    assert evaluated == 52


def test_inventory_decimal_gap_and_same_unit_buyer_override_are_explicit():
    base = snapshot()
    report = assess_company(base, None, NOW)
    copper = next(alert for alert in report['alerts'] if alert['category'] == 'inventory'
                  and alert['material_ids'] == ['copper'])
    assert copper['metrics']['unit'] == 'kg'
    assert copper['metrics']['stock'] == '2000'
    assert copper['metrics']['shortest_lead_days'] == 10
    assert copper['provenance'] == 'synthetic_profile'
    scenario = base['company']['quote_coverage']['scenarios'][0]['buyer_defaults']
    base['buyer_draft'] = {key: scenario[key] for key in (
        'material', 'quantity', 'current_stock', 'quantity_unit', 'budget',
        'additional_cost_per_unit', 'decision_date', 'need_by', 'reporting_currency')}
    base['buyer_draft']['current_stock'] = '100'
    revised = assess_company(base, None, NOW)
    updated = next(alert for alert in revised['alerts'] if alert['category'] == 'inventory'
                   and alert['material_ids'] == ['copper'])
    assert updated['metrics']['stock'] == '100'
    assert updated['metrics']['stock_source'] == 'buyer_synthetic_assumption'
    buyer_evidence = updated['evidence'][0]
    assert 'buyer' in buyer_evidence['label'].lower()
    assert 'synthetic assumption' in buyer_evidence['detail'].lower()
    assert '100 kg' in buyer_evidence['detail']
    assert 'buyer' in updated['confidence']['reason'].lower()
    base['buyer_draft']['quantity_unit'] = 'bale'
    invalid = assess_company(base, None, NOW)
    assert any('buyer draft' in gap.lower() for gap in invalid['data_gaps'])
    assert next(alert for alert in invalid['alerts'] if alert['category'] == 'inventory'
                and alert['material_ids'] == ['copper'])['metrics']['stock'] == '2000'


def test_dated_order_lead_exposure_is_hypothetical_and_uses_scenario_date():
    value = snapshot()
    value['company']['orders'][0]['need_by'] = '2026-10-16'
    result = assess_company(value, None, NOW)
    alert = next(alert for alert in result['alerts'] if alert['category'] == 'supplier_delay'
                 and alert['evidence'][0]['kind'] == 'order')
    assert alert['metrics']['earliest_arrival'] == '2026-10-24'
    assert alert['metrics']['lateness_days'] == 8
    assert alert['metrics']['unit'] == 'kg'
    assert 'potential' in alert['impact'].lower() or 'could' in alert['impact'].lower()
    assert 'confirmed' not in alert['impact'].lower()
    assert alert['provenance'] == 'synthetic_delay_scenario'
    assert len([item for item in result['alerts'] if item['category'] == 'supplier_delay'
                and item['evidence'][0]['ref_id'] == value['company']['orders'][0]['order_id']]) == 1


def test_all_ten_companies_surface_dated_slow_supplier_options_with_fast_alternatives():
    for company in list_companies():
        report = assess_company(snapshot(company['company_id']), None, NOW)
        slow_alerts = [alert for alert in report['alerts']
                       if alert['category'] == 'supplier_delay'
                       and alert['metrics'].get('supplier_id')]
        assert slow_alerts, company['company_id']
        for scenario in company['quote_coverage']['scenarios']:
            scenario_orders = [order for order in company['orders']
                               if order['material_id'] == scenario['material_id']
                               and order['need_by'] == scenario['buyer_defaults']['need_by']]
            assert scenario_orders
            for order in scenario_orders:
                assert len([alert for alert in slow_alerts
                            if alert['evidence'][0]['ref_id'] == order['order_id']]) == 1
        repeated = assess_company(snapshot(company['company_id']), None, NOW)
        assert {alert['alert_id'] for alert in slow_alerts} == {
            alert['alert_id'] for alert in repeated['alerts']
            if alert['category'] == 'supplier_delay' and alert['metrics'].get('supplier_id')}
        for alert in slow_alerts:
            supplier = next(row for row in company['suppliers']
                            if row['supplier_id'] == alert['metrics']['supplier_id'])
            assert alert['metrics']['supplier_name'] == supplier['name']
            assert alert['metrics']['supplier_lead_days'] == supplier['lead_time_days']
            assert alert['metrics']['scenario_date'] == company['as_of']
            assert alert['metrics']['projected_arrival'] > alert['metrics']['need_by']
            assert alert['metrics']['faster_option_count'] >= 1
            assert alert['severity'] in {'low', 'medium'}
            assert alert['evidence'][1]['ref_id'] == supplier['supplier_id']
            assert supplier['name'] in alert['evidence'][1]['label']
            assert str(supplier['lead_time_days']) in alert['evidence'][1]['detail']
            assert 'potential' in alert['impact'].lower()
            assert 'faster' in alert['impact'].lower()
            assert 'selected' not in alert['impact'].lower()
            assert all(action['requires_review'] for action in alert['recovery_actions'])


def test_no_slow_supplier_watch_after_all_leads_or_for_unbound_order():
    value = snapshot()
    order = value['company']['orders'][0]
    value['company']['orders'] = [order]
    order['need_by'] = '2027-01-01'
    report = assess_company(value, None, NOW)
    assert not [alert for alert in report['alerts'] if alert['category'] == 'supplier_delay']
    for changes in ({'unit': 'bale'}, {'material_id': 'foreign-material'}):
        other = snapshot()
        other['company']['orders'] = [{**other['company']['orders'][0], **changes}]
        result = assess_company(other, None, NOW)
        assert not [alert for alert in result['alerts'] if alert['category'] == 'supplier_delay']
        assert any('same-unit' in gap for gap in result['data_gaps'])


def test_news_requires_owned_relevance_fresh_metadata_and_source_quality():
    value = snapshot()
    route = value['company']['routes'][0]['route_id']
    def item(item_id, **fields):
        return {'item_id': item_id, 'url': f'https://example.org/{item_id}',
                'title': 'Port disruption may affect route', 'provider': 'GDELT',
                'synthetic': False,
                'publisher': 'Example', 'published_at': '2026-10-14T08:00:00+00:00',
                'retrieved_at': '2026-10-14T09:00:00+00:00',
                'matched_materials': [], 'matched_routes': [], 'matched_currencies': [],
                'matched_operations': [], 'relevance_kind': 'unmatched', **fields}
    news = {'cache_state': 'fresh', 'sources': [{'source_id': 'gdelt', 'state': 'fresh',
             'name': 'GDELT', 'last_checked_at': NOW.isoformat()}], 'items': [
        item('direct', matched_materials=['copper'], relevance_kind='direct_material'),
        item('route', matched_routes=[route], matched_operations=['logistics'], relevance_kind='route'),
        item('currency', matched_currencies=['EUR'], relevance_kind='currency'),
        item('unmatched'),
        item('foreign', matched_routes=['foreign-route'], matched_operations=['logistics'], relevance_kind='route'),
        item('future', matched_materials=['copper'], relevance_kind='direct_material',
             published_at='2026-10-15T08:00:00+00:00'),
        item('stale', matched_materials=['copper'], relevance_kind='direct_material',
             published_at='2026-10-01T08:00:00+00:00'),
        item('duplicate', url='https://example.org/direct', matched_materials=['copper'],
             relevance_kind='direct_material'),
    ]}
    result = assess_company(value, news, NOW)
    alerts = [alert for alert in result['alerts'] if alert['category'] == 'news']
    assert {alert['evidence'][0]['ref_id'] for alert in alerts} == {'direct', 'route', 'currency'}
    assert all(alert['confidence']['level'] == 'low' for alert in alerts)
    assert all(alert['provenance'] == 'observed_news' for alert in alerts)
    assert all('conditional' in alert['impact'].lower() for alert in alerts)
    assert all(alert['metrics']['source_quality'] == 'aggregated_headline_metadata'
               for alert in alerts)
    assert result['coverage']['news_items_evaluated'] == len(news['items'])
    news['sources'][0]['state'] = 'stale'
    assert not [a for a in assess_company(value, news, NOW)['alerts'] if a['category'] == 'news']


def test_resolved_headlines_are_low_context_and_active_headline_is_at_most_medium():
    value = snapshot()
    titles = {
        'averted': 'BHP averts strike at world’s biggest copper mine',
        'restarted': 'LG Chem restarts Yeosu naphtha cracker after seven-month halt',
        'called-off': 'Copper mine strike called off after agreement',
        'no-shortage': 'Copper supply update: no shortage expected',
        'active': 'Copper mine strike halts deliveries',
    }
    news = {'cache_state': 'fresh', 'sources': [{'source_id': 'gdelt', 'state': 'fresh'}],
            'items': [
                {'item_id': item_id, 'url': f'https://example.org/{item_id}',
                 'title': title, 'provider': 'GDELT', 'synthetic': False,
                 'published_at': '2026-10-14T08:00:00+00:00',
                 'retrieved_at': '2026-10-14T09:00:00+00:00',
                 'matched_materials': ['copper'], 'relevance_kind': 'direct_material'}
                for item_id, title in titles.items()]}
    alerts = {alert['evidence'][0]['ref_id']: alert for alert in
              assess_company(value, news, NOW)['alerts'] if alert['category'] == 'news'}
    assert set(alerts) == set(titles)
    for item_id in ('averted', 'restarted', 'called-off', 'no-shortage'):
        assert alerts[item_id]['severity'] == 'low'
        assert 'easing' in alerts[item_id]['impact'].lower()
        assert 'outstanding' in alerts[item_id]['impact'].lower()
    assert alerts['active']['severity'] == 'medium'
    assert 'easing' not in alerts['active']['impact'].lower()


def test_active_route_disruption_out_ranks_newer_resolved_direct_headlines():
    value = snapshot()
    route_id = value['company']['routes'][0]['route_id']
    resolved = [
        {'item_id': f'resolved-{index}', 'url': f'https://example.org/resolved-{index}',
         'title': 'BHP averts strike at copper mine', 'provider': 'GDELT',
         'synthetic': False, 'published_at': '2026-10-14T09:00:00+00:00',
         'retrieved_at': '2026-10-14T09:05:00+00:00',
         'matched_materials': ['copper'], 'relevance_kind': 'direct_material'}
        for index in range(5)]
    active = {'item_id': 'active-route', 'url': 'https://example.org/active-route',
              'title': 'Port closure disrupts copper route', 'provider': 'GDELT',
              'synthetic': False, 'published_at': '2026-10-14T07:00:00+00:00',
              'retrieved_at': '2026-10-14T08:00:00+00:00',
              'matched_routes': [route_id], 'matched_operations': ['logistics'],
              'relevance_kind': 'route'}
    news = {'cache_state': 'fresh', 'sources': [{'source_id': 'gdelt', 'state': 'fresh'}],
            'items': [*resolved, active]}
    alerts = [alert for alert in assess_company(value, news, NOW)['alerts']
              if alert['category'] == 'news']
    assert len(alerts) == 5
    assert alerts[0]['evidence'][0]['ref_id'] == 'active-route'
    assert alerts[0]['severity'] == 'medium'
    assert all(alert['severity'] != 'high' for alert in alerts)


def test_stale_news_cache_keeps_fresh_source_and_stale_duplicate_cannot_hide_it():
    value = snapshot()
    news = {'cache_state': 'stale', 'sources': [
        {'source_id': 'gdelt', 'state': 'stale'}, {'source_id': 'eia', 'state': 'fresh'}],
        'items': [
            {'item_id': 'old-copy', 'url': 'https://example.org/same',
             'title': 'Old copper supply concern', 'provider': 'GDELT', 'synthetic': False,
             'published_at': '2026-10-01T08:00:00+00:00',
             'retrieved_at': '2026-10-01T09:00:00+00:00',
             'matched_materials': ['copper'], 'relevance_kind': 'direct_material'},
            {'item_id': 'fresh-copy', 'url': 'https://example.org/same',
             'title': 'Current copper supply concern', 'provider': 'EIA', 'synthetic': False,
             'published_at': '2026-10-14T08:00:00+00:00',
             'retrieved_at': '2026-10-14T09:00:00+00:00',
             'matched_materials': ['copper'], 'relevance_kind': 'direct_material'},
        ]}
    result = assess_company(value, news, NOW)
    assert [alert['evidence'][0]['ref_id'] for alert in result['alerts']
            if alert['category'] == 'news'] == ['fresh-copy']
    assert result['coverage']['news_state'] == 'stale'


def test_sector_news_needs_explicit_company_operation_reason():
    value = snapshot()
    item = {'item_id': 'sector-one', 'url': 'https://example.org/sector-one',
            'title': 'Cable manufacturing operations disruption', 'provider': 'EIA',
            'synthetic': False,
            'published_at': '2026-10-14T08:00:00+00:00',
            'retrieved_at': '2026-10-14T09:00:00+00:00',
            'matched_materials': [], 'matched_routes': [], 'matched_currencies': [],
            'matched_operations': ['production'], 'relevance_kind': 'sector',
            'relevance_reason': 'Headline connects the company sector to operations: production'}
    news = {'cache_state': 'fresh', 'sources': [{'source_id': 'eia', 'state': 'fresh'}],
            'items': [item]}
    relevant = assess_company(value, news, NOW)
    alert = next(alert for alert in relevant['alerts'] if alert['category'] == 'news')
    assert alert['material_ids'] == [] and alert['severity'] == 'low'
    news['items'][0]['relevance_reason'] = 'Unrelated company sector.'
    rejected = assess_company(value, news, NOW)
    assert not any(alert['category'] == 'news' for alert in rejected['alerts'])


def test_google_news_item_binds_exact_source_id_and_health():
    value = snapshot()
    news = {'cache_state': 'fresh', 'sources': [
        {'source_id': 'google_news_1', 'state': 'fresh', 'name': 'Company news'},
        {'source_id': 'google_news_2', 'state': 'stale', 'name': 'Other news'}],
        'items': [{'item_id': 'google-copper', 'url': 'https://example.org/google-copper',
                   'title': 'Copper supply concern', 'provider': 'Google News',
                   'synthetic': False,
                   'source_id': 'google_news_1', 'publisher': 'Example',
                   'published_at': '2026-10-14T08:00:00+00:00',
                   'retrieved_at': '2026-10-14T09:00:00+00:00',
                   'matched_materials': ['copper'], 'relevance_kind': 'direct_material'}]}
    report = assess_company(value, news, NOW)
    item = next(alert for alert in report['alerts'] if alert['category'] == 'news')
    assert item['metrics']['source_id'] == 'google_news_1'
    assert item['metrics']['source_state'] == 'fresh'
    assert item['metrics']['source_quality'] == 'company_news_rss_metadata'
    news['sources'][0]['state'] = 'stale'
    assert not any(alert['category'] == 'news'
                   for alert in assess_company(value, news, NOW)['alerts'])


def test_only_explicit_nonsynthetic_headlines_get_observed_news_alerts():
    news = {'cache_state': 'fresh', 'sources': [{'source_id': 'gdelt', 'state': 'fresh'}],
            'items': [
                {'item_id': item_id, 'url': f'https://example.org/{item_id}',
                 'title': 'Copper shortage', 'provider': 'GDELT',
                 'published_at': '2026-10-14T08:00:00+00:00',
                 'retrieved_at': '2026-10-14T09:00:00+00:00',
                 'matched_materials': ['copper'], 'relevance_kind': 'direct_material',
                 **flag}
                for item_id, flag in [('observed', {'synthetic': False}),
                                      ('synthetic', {'synthetic': True}),
                                      ('missing', {})]]}
    alerts = [alert for alert in assess_company(snapshot(), news, NOW)['alerts']
              if alert['category'] == 'news']
    assert [alert['evidence'][0]['ref_id'] for alert in alerts] == ['observed']


def test_foreign_or_stale_plan_versions_never_become_recovery_evidence():
    value = snapshot()
    value['last_plan'] = {'plan_id': 'foreign-plan', 'company_id': 'another-company',
                          'material': 'copper', 'selected_offer_id': 'foreign-offer',
                          'offer_versions': {'foreign-offer': 2}}
    value['recovery'] = {'scenario': {'selected_offer_id': 'foreign-offer'},
                         'scenario_request': {'delay_days_by_offer': {'foreign-offer': 20}}}
    result = assess_company(value, None, NOW)
    assert not any(alert['provenance'] == 'synthetic_plan' for alert in result['alerts'])
    assert any('plan' in gap.lower() for gap in result['data_gaps'])


def test_malformed_offer_binding_and_news_metadata_fail_closed():
    value = snapshot()
    value['last_plan'] = {'plan_id': 'bad-plan', 'company_id': value['company_id'],
                          'material': 'copper', 'selected_offer_id': 'one',
                          'offer_versions': {'one': 2}}
    value['plan_request'] = {'material': 'copper', 'offer_ids': [{'bad': 'id'}],
                             'expected_versions': {'one': 2}}
    news = {'cache_state': 'fresh', 'sources': [{'source_id': {'bad': 'id'}, 'state': 'fresh'}],
            'items': [{'item_id': 'bad-news', 'url': 'https://example.org/bad',
                       'title': 'Copper shortage', 'source_id': {'bad': 'id'},
                       'published_at': '2026-10-14T08:00:00+00:00',
                       'retrieved_at': '2026-10-14T09:00:00+00:00',
                       'matched_materials': ['copper'], 'relevance_kind': 'direct_material'}]}
    result = assess_company(value, news, NOW)
    assert not any(alert['category'] == 'news' for alert in result['alerts'])
    assert any('plan' in gap.lower() for gap in result['data_gaps'])


def test_valid_recorded_recovery_uses_real_synthetic_projection_and_versions():
    value = snapshot()
    day = NOW.date()
    offers = []
    for fixture_id in ('late-copper', 'express-copper', 'eu-copper'):
        offer = quote_service.new_offer(
            quote_service.extract_fixture(fixture_id, mode='rules', as_of=day),
            offer_id=fixture_id, today=day)
        for field, item in quote_service.fixture_review_values(fixture_id).items():
            offer = quote_service.correct_offer(offer, field=field, value=item, today=day)
        offers.append(quote_service.save_offer(offer, today=day))
    value['offers'] = offers
    defaults = value['company']['quote_coverage']['scenarios'][0]['buyer_defaults']
    request = {**defaults, 'offer_ids': [offer['offer_id'] for offer in offers],
               'expected_versions': {offer['offer_id']: offer['version'] for offer in offers}}
    baseline = planner.build_plan(offers, request, today=day)
    value['last_plan'] = baseline
    value['plan_request'] = request
    value['recovery'] = recovery.compare(
        baseline, request, offers, {baseline['selected_offer_id']: 20}, today=day)
    value['plan'] = value['recovery']['scenario']
    assert value['plan']['selected_offer_id'] != baseline['selected_offer_id']
    report = assess_company(value, None, NOW)
    alert = next(alert for alert in report['alerts'] if alert['provenance'] == 'synthetic_plan')
    assert alert['metrics']['baseline_offer_id'] == baseline['selected_offer_id']
    assert alert['evidence'][0]['ref_id'] == baseline['plan_id']
    assert alert['metrics']['scenario_offer_id'] == value['recovery']['scenario']['selected_offer_id']
    assert alert['metrics']['cost_delta'] == value['recovery']['cost_delta']
    assert alert['metrics']['selection_changed'] in {'true', 'false'}
    assert alert['material_ids'] == ['copper']
    assert all(value is None or type(value) in (str, int, float)
               for value in alert['metrics'].values())
    value['offers'][0]['version'] += 1
    stale = assess_company(value, None, NOW)
    assert not any(alert['provenance'] == 'synthetic_plan' for alert in stale['alerts'])
    assert any('version' in gap.lower() for gap in stale['data_gaps'])
