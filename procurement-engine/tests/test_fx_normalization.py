"""Synthetic fake originals only: no sockets, observed rows, models, or services."""
from __future__ import annotations

import csv
import copy
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from procure.evaluation import fx_normalization as fx
from procure.evaluation import source_transport as transport


HEADERS = ["KEY", "FREQ", "CURRENCY", "CURRENCY_DENOM", "EXR_TYPE", "EXR_SUFFIX",
           "TIME_PERIOD", "OBS_VALUE", "UNIT", "UNIT_MULT", "OBS_STATUS"]
SCHEMA = {
    "schema_version": "synthetic-ecb-schema.v1", "synthetic": True,
    "columns": HEADERS, "frequency": "D", "denominator": "EUR",
    "exr_type": "SP00", "exr_suffix": "A", "unit": "SYNTHETIC_QUOTE",
    "unit_mult": "0", "obs_status": "A",
    "pairs": {
        "ecb_usd_eur": {"currency": "USD", "key": "D.USD.EUR.SP00.A", "meaning": "USD_per_EUR"},
        "ecb_inr_eur": {"currency": "INR", "key": "D.INR.EUR.SP00.A", "meaning": "INR_per_EUR"},
    },
}
COVERAGE = {
    "schema_version": "synthetic-date-mask.v1", "synthetic": True,
    "as_of": "2026-10-08", "required_months": ["2026-08"],
    "dates_by_month": {"2026-08": ["2026-08-03", "2026-08-04"]},
}
PROVENANCE = {
    "synthetic": True, "source_authenticity": False, "observed_eligible": False,
    "execution_authorized": False, "rights": "synthetic_only",
    "retrieved_at": "2026-10-08T00:00:00Z",
}


class FakeResponse:
    def __init__(self, body: bytes):
        self.body, self.offset = body, 0
        self.status, self.complete = 200, True
        self.headers = [("Content-Type", "text/csv"), ("Content-Length", str(len(body)))]
    def read(self, n):
        part = self.body[self.offset:self.offset+n]
        self.offset += len(part)
        return part
    def close(self):
        pass


def csv_body(currency: str, values: list[tuple[str, str]]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(HEADERS)
    for day, value in values:
        writer.writerow([f"D.{currency}.EUR.SP00.A", "D", currency, "EUR", "SP00", "A",
                         day, value, "SYNTHETIC_QUOTE", "0", "A"])
    return out.getvalue().encode("utf-8")


class SyntheticFxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-fx-panel-")
        self.root = Path(self.tmp.name)
        self.archive = self.root / "source-archive"
        self.output = self.root / "panel-output"
    def tearDown(self):
        self.tmp.cleanup()
    def archive_pair(self, usd=None, inr=None, run_id="sources"):
        usd = usd if usd is not None else csv_body("USD", [("2026-08-03", "2"), ("2026-08-04", "4")])
        inr = inr if inr is not None else csv_body("INR", [("2026-08-03", "100"), ("2026-08-04", "120")])
        stage = self.root / ("stage-" + run_id)
        stage.mkdir()
        originals, captures = {}, {}
        for source_id, body in (("ecb_usd_eur", usd), ("ecb_inr_eur", inr)):
            path = stage / (source_id + ".raw")
            path.write_bytes(body)
            originals[source_id] = path
            with tempfile.TemporaryFile(dir=self.root) as witness:
                captures[source_id] = transport.capture_response(FakeResponse(body), witness.fileno())
        transport.publish_offline(self.archive, run_id, originals,
                                  {"offline_only": True, "settled": True, "captures": captures})
        return fx.load_verified_originals(self.archive, run_id, PROVENANCE)

    def test_daily_cross_mean_is_40_not_ratio_of_monthly_means(self):
        pair = self.archive_pair()
        candidate = fx.normalize_ecb_pair(pair, SCHEMA, COVERAGE)
        month = candidate["months"][0]
        self.assertEqual((month["month"], month["status"], month["value"], month["unit"]),
                         ("2026-08", "complete", "40", "INR/USD"))
        self.assertNotEqual(month["value"], "36.666666666666666666666666666666666666666666666667")
        self.assertEqual(month["matched_days"], 2)
        self.assertEqual(month["available_at_proxy"], "2026-09-30T23:59:59.999999Z")

    def test_both_sources_missing_same_expected_day_is_unavailable_without_value(self):
        usd = csv_body("USD", [("2026-08-03", "2")])
        inr = csv_body("INR", [("2026-08-03", "100")])
        candidate = fx.normalize_ecb_pair(self.archive_pair(usd, inr), SCHEMA, COVERAGE)
        month = candidate["months"][0]
        self.assertEqual(month["status"], "unavailable")
        self.assertIsNone(month["value"])
        self.assertIn("EXPECTED_DATE_MISSING", month["reasons"])

    def test_one_source_complete_archive_cannot_load_as_pair(self):
        raw = self.root / "only-usd.raw"
        body = csv_body("USD", [("2026-08-03", "2")])
        raw.write_bytes(body)
        with tempfile.TemporaryFile(dir=self.root) as witness:
            captured = transport.capture_response(FakeResponse(body), witness.fileno())
        receipt = transport.publish_offline(self.archive, "partial", {"ecb_usd_eur": raw},
                                            {"offline_only": True, "settled": True,
                                             "captures": {"ecb_usd_eur": captured}})
        self.assertEqual(receipt["status"], "OFFLINE_INCOMPLETE")
        with self.assertRaises(fx.FxPanelError):
            fx.load_verified_originals(self.archive, "partial", PROVENANCE)

    def test_observed_provenance_is_refused_before_loading(self):
        self.archive_pair()
        observed = {**PROVENANCE, "synthetic": False}
        with self.assertRaises(fx.FxPanelError):
            fx.load_verified_originals(self.archive, "sources", observed)

    def test_observed_flags_in_pair_and_candidate_are_refused(self):
        pair = self.archive_pair()
        pair["source_authenticity"] = True
        with self.assertRaisesRegex(fx.FxPanelError, "OBSERVED_HOLD"):
            fx.normalize_ecb_pair(pair, SCHEMA, COVERAGE)
        pair["source_authenticity"] = False
        candidate = fx.normalize_ecb_pair(pair, SCHEMA, COVERAGE)
        candidate["observed_eligible"] = True
        with self.assertRaisesRegex(fx.FxPanelError, "OBSERVED_HOLD"):
            fx.publish_fx_panel(candidate, PROVENANCE, self.output, "observed")

    def test_mixed_type_calendar_is_sanitized(self):
        pair = self.archive_pair()
        coverage = copy.deepcopy(COVERAGE)
        coverage["required_months"] = [1, "2026-08"]
        coverage["dates_by_month"][1] = ["2026-08-03"]
        with self.assertRaises(fx.FxPanelError) as caught:
            fx.normalize_ecb_pair(pair, SCHEMA, coverage)
        self.assertEqual(str(caught.exception), "COVERAGE_INVALID")

    def test_schema_boolean_alias_is_rejected(self):
        pair = self.archive_pair()
        schema = copy.deepcopy(SCHEMA)
        schema["synthetic"] = 1
        with self.assertRaisesRegex(fx.FxPanelError, "SCHEMA_INVALID"):
            fx.normalize_ecb_pair(pair, schema, COVERAGE)

    def test_future_row_is_rejected_before_month_output(self):
        future = csv_body("USD", [("2026-08-03", "2"), ("2026-08-04", "4"),
                                  ("2026-10-01", "3")])
        with self.assertRaisesRegex(fx.FxPanelError, "CSV_DATE_SET"):
            fx.normalize_ecb_pair(self.archive_pair(usd=future), SCHEMA, COVERAGE)

    def test_unapproved_status_and_overprecision_are_rejected(self):
        status = csv_body("USD", [("2026-08-03", "2"), ("2026-08-04", "4")])
        status = status.replace(b",A\n", b",P\n", 1)
        pair = self.archive_pair(usd=status, run_id="bad-status")
        with self.assertRaisesRegex(fx.FxPanelError, "CSV_METADATA"):
            fx.normalize_ecb_pair(pair, SCHEMA, COVERAGE)
        long_value = "1." + "2" * 39
        pair = self.archive_pair(
            usd=csv_body("USD", [("2026-08-03", long_value), ("2026-08-04", "4")]),
            run_id="long-value")
        with self.assertRaisesRegex(fx.FxPanelError, "RATE_LIMIT"):
            fx.normalize_ecb_pair(pair, SCHEMA, COVERAGE)

    def test_date_set_mismatch_is_unavailable(self):
        usd = csv_body("USD", [("2026-08-03", "2"), ("2026-08-04", "4")])
        inr = csv_body("INR", [("2026-08-03", "100")])
        month = fx.normalize_ecb_pair(self.archive_pair(usd=usd, inr=inr),
                                      SCHEMA, COVERAGE)["months"][0]
        self.assertEqual(month["status"], "unavailable")
        self.assertIsNone(month["value"])
        self.assertIn("DATE_SET_MISMATCH", month["reasons"])

    def test_fraction_rounding_and_value_bound(self):
        pair = self.archive_pair(
            csv_body("USD", [("2026-08-03", "3"), ("2026-08-04", "3")]),
            csv_body("INR", [("2026-08-03", "1"), ("2026-08-04", "1")]))
        value = fx.normalize_ecb_pair(pair, SCHEMA, COVERAGE)["months"][0]["value"]
        self.assertEqual(value, "0." + "3" * 50)
        pair["originals"]["ecb_usd_eur"]["body"] = csv_body(
            "USD", [("2026-08-03", "1e2"), ("2026-08-04", "3")])
        with self.assertRaises(fx.FxPanelError):
            fx.normalize_ecb_pair(pair, SCHEMA, COVERAGE)

    def test_source_symlink_is_refused(self):
        self.archive_pair()
        original = self.archive / "objects/sources/ecb_usd_eur.raw"
        replacement = self.root / "replaced.raw"
        replacement.write_bytes(original.read_bytes())
        original.parent.chmod(0o700)
        original.chmod(0o600)
        original.unlink()
        original.symlink_to(replacement)
        original.parent.chmod(0o500)
        with self.assertRaises(fx.FxPanelError):
            fx.load_verified_originals(self.archive, "sources", PROVENANCE)

    def test_same_bytes_replaced_inode_during_verification_is_refused(self):
        self.archive_pair()
        target = self.archive / "objects/sources/ecb_usd_eur.raw"
        verify = transport.verify_publication
        def swap_after_verification(root, run_id):
            receipt = verify(root, run_id)
            body = target.read_bytes()
            target.parent.chmod(0o700)
            target.unlink()
            target.write_bytes(body)
            target.chmod(0o400)
            target.parent.chmod(0o500)
            return receipt
        with patch.object(fx.source_transport, "verify_publication",
                          side_effect=swap_after_verification):
            with self.assertRaisesRegex(fx.FxPanelError, "FILE_CHANGED"):
                fx.load_verified_originals(self.archive, "sources", PROVENANCE)

    def test_unavailable_panel_cannot_have_complete_receipt(self):
        pair = self.archive_pair(
            csv_body("USD", [("2026-08-03", "2")]),
            csv_body("INR", [("2026-08-03", "100")]))
        candidate = fx.normalize_ecb_pair(pair, SCHEMA, COVERAGE)
        receipt = fx.publish_fx_panel(candidate, PROVENANCE, self.output, "partial-panel")
        self.assertEqual(receipt["status"], "SYNTHETIC_OFFLINE_INCOMPLETE")
        self.assertFalse((self.output / "complete/partial-panel.json").exists())
        with self.assertRaises(fx.FxPanelError):
            fx.verify_fx_panel(self.output, "partial-panel", pair, SCHEMA, COVERAGE)

    def test_marker_fsync_failure_does_not_leave_complete_receipt(self):
        pair = self.archive_pair()
        candidate = fx.normalize_ecb_pair(pair, SCHEMA, COVERAGE)
        real_fsync = os.fsync
        calls = 0
        def injected(fd):
            nonlocal calls
            calls += 1
            if calls == 6:
                raise OSError("synthetic fsync fault")
            return real_fsync(fd)
        with patch.object(fx.os, "fsync", side_effect=injected):
            with self.assertRaisesRegex(fx.FxPanelError, "PUBLICATION_IO"):
                fx.publish_fx_panel(candidate, PROVENANCE, self.output, "fault")
        self.assertFalse((self.output / "complete/fault.json").exists())
        with self.assertRaises(fx.FxPanelError):
            fx.verify_fx_panel(self.output, "fault", pair, SCHEMA, COVERAGE)

    def test_complete_synthetic_panel_is_no_clobber_and_rehashes(self):
        pair = self.archive_pair()
        candidate = fx.normalize_ecb_pair(pair, SCHEMA, COVERAGE)
        receipt = fx.publish_fx_panel(candidate, PROVENANCE, self.output, "panel")
        self.assertEqual(receipt["status"], "SYNTHETIC_OFFLINE_ONLY")
        self.assertFalse(receipt["execution_authorized"])
        self.assertFalse(receipt["source_authenticity"])
        self.assertEqual(fx.verify_fx_panel(self.output, "panel", pair, SCHEMA, COVERAGE)["status"],
                         "SYNTHETIC_OFFLINE_ONLY")
        before = (self.output / "complete/panel.json").read_bytes()
        with self.assertRaises(fx.FxPanelError):
            fx.publish_fx_panel(candidate, PROVENANCE, self.output, "panel")
        self.assertEqual((self.output / "complete/panel.json").read_bytes(), before)
        panel = self.output / "objects/panel/panel.csv"
        panel.chmod(0o600)
        panel.write_bytes(b"tampered\n")
        with self.assertRaises(fx.FxPanelError):
            fx.verify_fx_panel(self.output, "panel", pair, SCHEMA, COVERAGE)


if __name__ == "__main__":
    unittest.main()
