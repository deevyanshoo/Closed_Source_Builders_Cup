"""Generated-only checks for the reusable monthly projection boundary."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import unittest

from procure.forecast.monthly_projection import (
    C2_QUANTILES,
    HORIZONS,
    TINY_QUANTILES,
    InvalidMonthlyProjection,
    assemble_persistence,
    foundation_candidates,
    ridge_candidates,
)


def sample(count=20, *, families=('commodity', 'fx')):
    months = [f'{2019 + i // 12:04d}-{i % 12 + 1:02d}' for i in range(count)]
    panel = {}
    metadata = {}
    for number, family in enumerate(families):
        sid = f'SYNTHETIC_{number}'
        panel[sid] = {month: f'{(100 + number * 20) * math.exp(.01 * i):.12f}'
                      for i, month in enumerate(months)}
        metadata[sid] = {'unit': 'synthetic_units', 'family': family,
                         'source_id': 'generated_test', 'source_sha256': 'a' * 64,
                         'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
                         'error_references': ['synthetic_only']}
    return panel, metadata


def recent_sample(*, last_offset):
    panel, metadata = sample()
    now = datetime.now(timezone.utc)
    final = now.year * 12 + now.month - 1 - last_offset
    first = final - 19
    dates = [f'{index // 12:04d}-{index % 12 + 1:02d}' for index in range(first, final + 1)]
    return ({sid: dict(zip(dates, values.values(), strict=True)) for sid, values in panel.items()}, metadata)


class MonthlyProjectionTest(unittest.TestCase):
    def test_persistence_has_current_generation_and_historical_issue(self):
        panel, metadata = sample()
        result = assemble_persistence(panel, metadata)
        self.assertEqual(result['selected_model'], 'persistence')
        self.assertEqual(result['candidate_status'], 'EXPERIMENTAL_NOT_PROMOTED')
        self.assertEqual(result['observation_end'], '2020-08')
        self.assertEqual(result['issue_month'], '2020-09')
        self.assertEqual(result['freshness_state'], 'HISTORICAL_NOT_CURRENT')
        self.assertEqual(result['as_of_date_utc'], datetime.now(timezone.utc).date().isoformat())
        self.assertEqual(len(result['selected']), 6)
        for row in result['selected']:
            self.assertEqual(row['target_month'], {1: '2020-10', 3: '2020-12', 6: '2021-03'}[row['horizon']])
            self.assertEqual(row['lead_months'], row['horizon'] + 1)
            self.assertEqual(float(row['native_price']), float(panel[row['series_id']]['2020-08']))
            self.assertEqual(row['provenance']['source_sha256'], 'a' * 64)
            self.assertEqual(row['error_references'], ['synthetic_only'])

    def test_invalid_panels_and_metadata_fail_closed(self):
        panel, metadata = sample()
        del panel['SYNTHETIC_0']['2019-05']
        with self.assertRaises(InvalidMonthlyProjection):
            assemble_persistence(panel, metadata)
        panel, metadata = sample()
        panel['SYNTHETIC_0']['2019-05'] = '0'
        with self.assertRaises(InvalidMonthlyProjection):
            assemble_persistence(panel, metadata)
        panel, metadata = sample()
        metadata['SYNTHETIC_0']['retrieved_at_utc'] = 1729
        with self.assertRaises(InvalidMonthlyProjection):
            assemble_persistence(panel, metadata)

    def test_fixed_ridge_uses_only_observed_labels_and_three_fits(self):
        panel, metadata = sample()
        result = ridge_candidates(panel, metadata)
        self.assertEqual(result['fit_attempts'], 6)
        self.assertEqual(result['training_anchors_per_series'], 7)
        self.assertEqual(len(result['candidates']), 6)
        for row in result['candidates']:
            anchor = float(panel[row['series_id']]['2020-08'])
            expected = anchor * math.exp(.01 * (row['horizon'] + 1))
            self.assertAlmostEqual(float(row['native_price']), expected, delta=1e-6)
            self.assertEqual(row['status'], 'EXPERIMENTAL_NOT_PROMOTED')

    def test_injected_models_validate_all_quantiles_and_call_counts(self):
        panel, metadata = sample()
        calls = []

        def tiny(histories, *, prediction_length, cross_learning):
            calls.append(('tiny', len(histories), prediction_length, cross_learning))
            return [[[[float(100 + q + lead) for lead in range(7)] for q in range(9)]]
                    for _ in histories]

        def c2(histories, *, prediction_length, cross_learning):
            calls.append(('c2', len(histories), prediction_length, cross_learning))
            return [[[[float(200 + q + lead) for lead in range(7)] for q in range(13)]]
                    for _ in histories]

        result = foundation_candidates(panel, metadata, tiny_predict=tiny,
                                       tiny_quantiles=list(TINY_QUANTILES),
                                       c2_predict=c2, c2_quantiles=list(C2_QUANTILES))
        self.assertEqual(calls, [('tiny', 2, 7, False), ('c2', 2, 7, False),
                                 ('c2', 1, 7, True), ('c2', 1, 7, True)])
        self.assertEqual(result['model_calls'], 4)
        self.assertEqual(result['contexts'], 6)
        self.assertEqual(result['fit_attempts'], 0)
        self.assertEqual(len(result['candidates']), 18)
        tiny_rows = [row for row in result['candidates'] if row['model'] == 'chronos_bolt_tiny']
        self.assertEqual([float(row['native_price']) for row in tiny_rows[:3]], [105., 107., 110.])

        def invalid_tiny(histories, *, prediction_length, cross_learning):
            output = tiny(histories, prediction_length=prediction_length, cross_learning=cross_learning)
            output[0][0][0][0] = float('nan')  # Non-median cells cannot be ignored.
            return output

        with self.assertRaises(InvalidMonthlyProjection):
            foundation_candidates(panel, metadata, tiny_predict=invalid_tiny,
                                  tiny_quantiles=list(TINY_QUANTILES),
                                  c2_predict=c2, c2_quantiles=list(C2_QUANTILES))

    def test_current_future_or_unsupported_retrieval_refuse_before_candidate_call(self):
        calls = []

        def no_call(*args, **kwargs):
            calls.append(True)
            raise AssertionError('candidate reached')

        for offset in (0, -1):
            panel, metadata = recent_sample(last_offset=offset)
            with self.assertRaises(InvalidMonthlyProjection):
                assemble_persistence(panel, metadata)
            with self.assertRaises(InvalidMonthlyProjection):
                ridge_candidates(panel, metadata)
            with self.assertRaises(InvalidMonthlyProjection):
                foundation_candidates(panel, metadata, tiny_predict=no_call,
                                      tiny_quantiles=list(TINY_QUANTILES),
                                      c2_predict=no_call, c2_quantiles=list(C2_QUANTILES))
        for retrieval in ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                          datetime.now(timezone(timedelta(hours=1))).isoformat()):
            panel, metadata = recent_sample(last_offset=1)
            metadata['SYNTHETIC_0']['retrieved_at_utc'] = retrieval
            with self.assertRaises(InvalidMonthlyProjection):
                assemble_persistence(panel, metadata)
            with self.assertRaises(InvalidMonthlyProjection):
                ridge_candidates(panel, metadata)
            with self.assertRaises(InvalidMonthlyProjection):
                foundation_candidates(panel, metadata, tiny_predict=no_call,
                                      tiny_quantiles=list(TINY_QUANTILES),
                                      c2_predict=no_call, c2_quantiles=list(C2_QUANTILES))
        self.assertEqual(calls, [])

    def test_previous_month_is_valid_for_baseline_and_both_candidate_entrypoints(self):
        panel, metadata = recent_sample(last_offset=1)

        def fake(quantile_count):
            def predict(histories, *, prediction_length, cross_learning):
                return [[[[100.0] * prediction_length for _ in range(quantile_count)]]
                        for _ in histories]
            return predict

        baseline = assemble_persistence(panel, metadata)
        self.assertEqual(baseline['freshness_state'], 'ISSUE_MONTH_EQUALS_AS_OF_UNVERIFIED')
        self.assertEqual(len(ridge_candidates(panel, metadata)['candidates']), 6)
        foundation = foundation_candidates(panel, metadata, tiny_predict=fake(9),
                                           tiny_quantiles=list(TINY_QUANTILES),
                                           c2_predict=fake(13), c2_quantiles=list(C2_QUANTILES))
        self.assertEqual(len(foundation['candidates']), 18)


if __name__ == '__main__':
    unittest.main()
