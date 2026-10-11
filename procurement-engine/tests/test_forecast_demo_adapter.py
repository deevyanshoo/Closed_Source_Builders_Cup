"""Generated-only, source-free cases for the forecast demo evidence adapter.

Prepared for a separate guarded review and execution release. No fixtures,
database, provider, observed artifact, or numerical model is imported here.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unittest

from procure.forecast.demo_adapter import (
    DemoEvidenceUnavailable, ForecastDemoBinding, build_demo_view,
    payload_digest, require_current_binding,
)


NOW = datetime(2026, 10, 11, 1, 0, tzinfo=timezone.utc)
IDS = ('synthetic:commodity', 'synthetic:fx')


def fixture():
    catalog = {
        IDS[0]: {'unit': '$/mt', 'family': 'commodity', 'source_id': 'generated-wb',
                 'source_sha256': 'a' * 64, 'retrieved_at_utc': '2026-10-09T00:00:00Z',
                 'error_references': ['sha256:' + 'c' * 64]},
        IDS[1]: {'unit': 'EUR/USD', 'family': 'fx', 'source_id': 'generated-bis',
                 'source_sha256': 'b' * 64, 'retrieved_at_utc': '2026-10-09T00:00:00Z',
                 'error_references': ['sha256:' + 'c' * 64]},
    }
    def row(sid, h, model='persistence', price='001.2300'):
        meta = catalog[sid]
        return {'series_id': sid, 'horizon': h, 'lead_months': h + 1,
                'observation_end': '2026-09', 'issue_month': '2026-10',
                'target_month': {1: '2026-11', 3: '2027-01', 6: '2027-04'}[h],
                'native_price': price, 'unit': meta['unit'], 'model': model,
                'status': 'SELECTED_BASELINE' if model == 'persistence' else 'EXPERIMENTAL_NOT_PROMOTED',
                'provenance': {'source_id': meta['source_id'],
                               'source_sha256': meta['source_sha256'],
                               'retrieved_at_utc': meta['retrieved_at_utc'],
                               'observed_through': '2026-09', 'unit': meta['unit'],
                               'family': meta['family']},
                'error_references': list(meta['error_references'])}
    projection = {'schema': 'monthly-projection.v1',
                  'generated_at_utc': '2026-10-11T00:00:00+00:00',
                  'as_of_date_utc': '2026-10-11', 'as_of_month_utc': '2026-10',
                  'observation_end': '2026-09', 'issue_month': '2026-10',
                  'freshness_state': 'ISSUE_MONTH_EQUALS_AS_OF_UNVERIFIED',
                  'selected_model': 'persistence', 'candidate_status': 'EXPERIMENTAL_NOT_PROMOTED',
                  'selected': [row(sid, h) for sid in IDS for h in (1, 3, 6)],
                  'limits': 'Generated fixture only.'}
    candidates = [row(IDS[0], 1, 'ridge_alpha_10', '2.500')]
    errors = [{'series_id': IDS[0], 'model': 'persistence', 'horizon': 1,
               'unit': '$/mt', 'ledger_sha256': 'c' * 64,
               'count': 24, 'p50': '0.01', 'p80': '0.02', 'p95': '0.03'}]
    return projection, candidates, catalog, errors


def bound_for(projection, candidates, catalog, errors, now=NOW):
    digest = payload_digest(projection, candidates, catalog, errors, now=now)
    return ForecastDemoBinding('generated-principal', 'generated-workspace',
                               'generated-context', 1, 'd' * 64, digest)


def render(projection, candidates, catalog, errors, *, now=NOW, sid=IDS[0], h=1):
    bound = bound_for(projection, candidates, catalog, errors, now)
    return build_demo_view(bound=bound, current=bound, projection=projection,
                           candidates=candidates, catalog=catalog,
                           historical_errors=errors, series_id=sid, horizon=h, now=now)


class DemoAdapterGeneratedTest(unittest.TestCase):
    def test_bound_persistence_and_separate_unpromoted_candidate(self):
        projection, candidates, catalog, errors = fixture()
        view = render(projection, candidates, catalog, errors)
        self.assertEqual(view['schema'], 'monthly-forecast-demo-view.v1')
        self.assertEqual(view['selected']['model'], 'persistence')
        self.assertEqual(view['selected']['native_price'], '001.2300')
        self.assertEqual(view['selected']['descriptive_error']['status'],
                         'DESCRIPTIVE_EXPOSED_HISTORY_ONLY')
        self.assertEqual(view['experimental'][0]['status'], 'EXPERIMENTAL_NOT_PROMOTED')
        self.assertEqual(view['experimental'][0]['descriptive_error']['status'], 'UNAVAILABLE')
        self.assertEqual(view['calendar']['target_month'], '2026-11')
        self.assertEqual(view['source']['publication_at_utc'], None)
        self.assertFalse(view['source']['live_market_quote'])
        fx = render(projection, candidates, catalog, errors, sid=IDS[1], h=6)
        self.assertEqual(fx['source']['unit'], 'EUR/USD')
        self.assertEqual(fx['experimental'], [])
        self.assertEqual(fx['calendar']['target_month'], '2027-04')

    def test_binding_refusal_precedes_any_payload_access_and_recheck(self):
        projection, candidates, catalog, errors = fixture()
        bound = bound_for(projection, candidates, catalog, errors)
        class Bomb:
            def __getattribute__(self, name):
                raise AssertionError('payload inspected before binding')
        variants = [
            replace(bound, principal_id='foreign-principal'),
            replace(bound, workspace_id='foreign-workspace'),
            replace(bound, context_id='foreign-context'),
            replace(bound, revision=2),
            replace(bound, context_digest_sha256='e' * 64),
            replace(bound, payload_digest_sha256='f' * 64),
        ]
        for current in variants:
            with self.subTest(field=current):
                with self.assertRaises(DemoEvidenceUnavailable):
                    build_demo_view(bound=bound, current=current, projection=Bomb(),
                                    candidates=candidates, catalog=catalog,
                                    historical_errors=errors, series_id=IDS[0],
                                    horizon=1, now=NOW)
                with self.assertRaises(DemoEvidenceUnavailable):
                    require_current_binding(bound, current)

    def test_digest_mutation_and_authoritative_unit_source_mismatch(self):
        projection, candidates, catalog, errors = fixture()
        bound = bound_for(projection, candidates, catalog, errors)
        candidates[0]['native_price'] = '2.501'
        with self.assertRaises(DemoEvidenceUnavailable):
            build_demo_view(bound=bound, current=bound, projection=projection,
                            candidates=candidates, catalog=catalog,
                            historical_errors=errors, series_id=IDS[0], horizon=1, now=NOW)
        projection, candidates, catalog, errors = fixture()
        projection['selected'][0]['unit'] = 'USD/mt'
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)
        projection, candidates, catalog, errors = fixture()
        projection['selected'][0]['provenance']['source_sha256'] = 'f' * 64
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)

    def test_duplicate_missing_or_wrong_calendar_refuses(self):
        projection, candidates, catalog, errors = fixture()
        projection['selected'].pop()
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)
        projection, candidates, catalog, errors = fixture()
        projection['selected'][1] = projection['selected'][0].copy()
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)
        projection, candidates, catalog, errors = fixture()
        candidates.append(candidates[0].copy())
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)
        projection, candidates, catalog, errors = fixture()
        projection['selected'][0]['target_month'] = '2026-12'
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)

    def test_utc_future_and_historical_recomputation(self):
        projection, candidates, catalog, errors = fixture()
        later = datetime(2026, 11, 2, tzinfo=timezone.utc)
        view = render(projection, candidates, catalog, errors, now=later)
        self.assertEqual(view['calendar']['freshness'], 'HISTORICAL_NOT_CURRENT')
        projection['as_of_date_utc'] = '2026-10-10'
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)
        projection, candidates, catalog, errors = fixture()
        catalog[IDS[0]]['retrieved_at_utc'] = '2026-10-12T00:00:00Z'
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)
        projection, candidates, catalog, errors = fixture()
        projection['generated_at_utc'] = '2026-10-11T02:00:00+00:00'
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)

    def test_decimal_exactness_and_descriptive_reference_boundaries(self):
        projection, candidates, catalog, errors = fixture()
        projection['selected'][0]['native_price'] = 'NaN'
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)
        projection, candidates, catalog, errors = fixture()
        errors[0]['unit'] = 'wrong-unit'
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)
        projection, candidates, catalog, errors = fixture()
        errors[0]['p80'] = '0.001'
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)
        projection, candidates, catalog, errors = fixture()
        errors[0]['model'] = 'unknown'
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)

    def test_excessive_or_unexpected_nested_schema_refuses(self):
        projection, candidates, catalog, errors = fixture()
        projection['selected'][0]['guessed_confidence'] = 0.9
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)
        projection, candidates, catalog, errors = fixture()
        catalog[IDS[0]]['extra'] = {'recursive': []}
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)
        projection, candidates, catalog, errors = fixture()
        candidates[:] = [candidates[0]] * 289
        with self.assertRaises(DemoEvidenceUnavailable):
            payload_digest(projection, candidates, catalog, errors, now=NOW)


    def test_generation_month_cannot_precede_observations(self):
        projection, candidates, catalog, errors = fixture()
        projection['generated_at_utc'] = '2026-09-30T00:00:00Z'
        projection['as_of_date_utc'] = '2026-09-30'
        projection['as_of_month_utc'] = '2026-09'
        projection['freshness_state'] = 'HISTORICAL_NOT_CURRENT'
        for meta in catalog.values():
            meta['retrieved_at_utc'] = '2026-09-29T00:00:00Z'
        for row in projection['selected'] + candidates:
            row['provenance']['retrieved_at_utc'] = '2026-09-29T00:00:00Z'
        with self.assertRaisesRegex(DemoEvidenceUnavailable, '^DEMO_EVIDENCE_UNAVAILABLE$'):
            payload_digest(projection, candidates, catalog, errors, now=NOW)

    def test_historical_error_ledger_must_match_catalog_reference(self):
        projection, candidates, catalog, errors = fixture()
        self.assertIn('sha256:' + errors[0]['ledger_sha256'],
                      catalog[IDS[0]]['error_references'])
        catalog[IDS[0]]['error_references'] = ['sha256:' + 'd' * 64]
        for row in projection['selected'] + candidates:
            if row['series_id'] == IDS[0]:
                row['error_references'] = list(catalog[IDS[0]]['error_references'])
        with self.assertRaisesRegex(DemoEvidenceUnavailable, '^DEMO_EVIDENCE_UNAVAILABLE$'):
            payload_digest(projection, candidates, catalog, errors, now=NOW)

    def test_text_and_object_bounds_refuse_before_encoding_or_iteration(self):
        projection, candidates, catalog, errors = fixture()
        class PoisonText(str):
            armed = False
            def encode(self, *args, **kwargs):
                if self.armed:
                    raise AssertionError('subclass encode called')
                return super().encode(*args, **kwargs)
        catalog[IDS[0]]['source_id'] = PoisonText('generated-wb')
        catalog[IDS[0]]['source_id'].armed = True
        with self.assertRaisesRegex(DemoEvidenceUnavailable, '^DEMO_EVIDENCE_UNAVAILABLE$'):
            payload_digest(projection, candidates, catalog, errors, now=NOW)

        projection, candidates, catalog, errors = fixture()
        catalog[IDS[0]]['source_id'] = 'bad\ud800'
        with self.assertRaisesRegex(DemoEvidenceUnavailable, '^DEMO_EVIDENCE_UNAVAILABLE$'):
            payload_digest(projection, candidates, catalog, errors, now=NOW)

        projection, candidates, catalog, errors = fixture()
        class PoisonDict(dict):
            def __iter__(self):
                raise AssertionError('subclass iteration called')
        projection['selected'][0] = PoisonDict(projection['selected'][0])
        with self.assertRaisesRegex(DemoEvidenceUnavailable, '^DEMO_EVIDENCE_UNAVAILABLE$'):
            payload_digest(projection, candidates, catalog, errors, now=NOW)

        projection, candidates, catalog, errors = fixture()
        class PoisonKey(str):
            armed = False
            def __hash__(self):
                if self.armed:
                    raise AssertionError('key rehashed')
                return super().__hash__()
        key = PoisonKey('status')
        row = projection['selected'][0]
        row[key] = row.pop('status')
        key.armed = True
        with self.assertRaisesRegex(DemoEvidenceUnavailable, '^DEMO_EVIDENCE_UNAVAILABLE$'):
            payload_digest(projection, candidates, catalog, errors, now=NOW)

        projection, candidates, catalog, errors = fixture()
        catalog[IDS[0]]['source_id'] = 'x' * 129
        with self.assertRaisesRegex(DemoEvidenceUnavailable, '^DEMO_EVIDENCE_UNAVAILABLE$'):
            payload_digest(projection, candidates, catalog, errors, now=NOW)

    def test_invalid_selectors_use_fixed_refusal(self):
        projection, candidates, catalog, errors = fixture()
        bound = bound_for(projection, candidates, catalog, errors)
        for sid, horizon in (([], 1), (IDS[0], True), (IDS[0], []),
                             ('x' * 129, 1)):
            with self.subTest(selector=sid, horizon=horizon):
                with self.assertRaisesRegex(DemoEvidenceUnavailable, '^DEMO_EVIDENCE_UNAVAILABLE$'):
                    build_demo_view(bound=bound, current=bound, projection=projection,
                                    candidates=candidates, catalog=catalog,
                                    historical_errors=errors, series_id=sid,
                                    horizon=horizon, now=NOW)


if __name__ == '__main__':
    unittest.main()
