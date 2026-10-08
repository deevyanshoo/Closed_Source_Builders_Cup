"""Generated synthetic commodity fixtures only; no observed-source reader or model."""
from __future__ import annotations

import csv
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from procure.evaluation import commodity_component as component


FLAGS = {"synthetic": True, "source_authenticity": False,
         "observed_eligible": False, "execution_authorized": False,
         "rights": "synthetic_only"}
SCHEMA = {"schema_version": "synthetic-commodity-component.v1", **FLAGS,
          "columns": ["month", "series_id", "unit", "status", "value"],
          "series_ids": ["copper", "aluminum"], "unit": "USD/metric_tonne"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def fake_csv(months=("2026-08",), values=None):
    values = values or {}
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(SCHEMA["columns"])
    for month in months:
        for series in SCHEMA["series_ids"]:
            value = values.get((month, series), "2000" if series == "copper" else "1500")
            writer.writerow([month, series, "USD/metric_tonne", "M" if value is None else "A",
                             "" if value is None else value])
    return stream.getvalue().encode("utf-8")


class CommodityComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-component-")
        self.root = Path(self.temp.name)
        self.archive = self.root / "fixture"
        self.output = self.root / "panel"
        self.fixture_count = 0
    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, body=None, months=("2026-08",), run_id=None):
        if run_id is None:
            self.fixture_count += 1
            run_id = f"fixture{self.fixture_count}"
        body = fake_csv(months) if body is None else body
        obj = self.archive / "objects" / run_id
        committed = self.archive / "committed"
        obj.mkdir(parents=True, mode=0o700)
        committed.mkdir(mode=0o700, exist_ok=True)
        for directory in (self.archive, self.archive / "objects", obj, committed):
            directory.chmod(0o700)
        (obj / "original.csv").write_bytes(body)
        (obj / "original.csv").chmod(0o600)
        receipt = {"schema_version": "synthetic-commodity-fixture-receipt.v1", **FLAGS,
                   "run_id": run_id, "first_month": months[0], "last_month": months[-1],
                   "month_count": len(months), "original_bytes": len(body),
                   "original_sha256": sha256(body).hexdigest()}
        (committed / f"{run_id}.json").write_bytes(canonical(receipt))
        (committed / f"{run_id}.json").chmod(0o600)
        return component.load_synthetic_component(self.archive, run_id, FLAGS)

    def candidate(self, body=None, months=("2026-08",), as_of="2026-10-08T00:00:00Z"):
        verified = self.fixture(body, months)
        return verified, component.normalize_commodity_component(verified, SCHEMA, list(months), as_of)

    def test_observed_flags_refused_before_file_open(self):
        bad = {**FLAGS, "source_authenticity": True}
        with patch.object(os, "open", side_effect=AssertionError("must not open")):
            with self.assertRaisesRegex(component.CommodityComponentError, "OBSERVED_HOLD"):
                component.load_synthetic_component(self.archive, "missing", bad)

    def test_exact_native_and_kg_arithmetic(self):
        _, candidate = self.candidate()
        copper, aluminum = candidate["months"]
        self.assertEqual((copper["native_value"], copper["usd_per_kg"]), ("2000", "2"))
        self.assertEqual((aluminum["native_value"], aluminum["usd_per_kg"]), ("1500", "1.5"))
        self.assertEqual((copper["native_basis"], copper["kg_basis"]),
                         ({"numerator": "2000", "denominator": "1"},
                          {"numerator": "2", "denominator": "1"}))
        self.assertFalse(candidate["execution_authorized"])

    def test_missing_and_future_are_explicit_nulls(self):
        body = fake_csv(values={("2026-08", "copper"): None})
        _, candidate = self.candidate(body)
        row = candidate["months"][0]
        self.assertEqual((row["status"], row["reason"], row["native_value"], row["usd_per_kg"]),
                         ("unavailable", "source_missing", None, None))
        _, future = self.candidate(as_of="2026-09-30T23:59:59.999998Z")
        self.assertTrue(all(row["status"] == "unavailable" and row["reason"] == "proxy_not_yet_available"
                            and row["native_value"] is None for row in future["months"]))
        _, edge = self.candidate(as_of="2026-09-30T23:59:59.999999Z")
        self.assertTrue(all(row["status"] == "complete" for row in edge["months"]))

    def test_wrong_unit_iron_duplicate_and_bad_tokens_refuse(self):
        for bad_row in ("2026-08,copper,USD/dmtu,A,2000\n",
                        "2026-08,iron_ore,USD/metric_tonne,A,2000\n",
                        "2026-08,copper,USD/metric_tonne,A,1e4\n",
                        "2026-08,copper,USD/metric_tonne,M,0\n"):
            body = ("month,series_id,unit,status,value\n" + bad_row +
                    "2026-08,aluminum,USD/metric_tonne,A,1500\n").encode()
            with self.subTest(bad_row=bad_row):
                with self.assertRaises(component.CommodityComponentError):
                    self.candidate(body)
                self.archive = self.root / ("fixture-" + sha256(body).hexdigest()[:8])

    def test_required_mask_and_asof_are_strict(self):
        verified = self.fixture()
        for mask, instant in (([], "2026-10-08T00:00:00Z"),
                              (["2026-07"], "2026-10-08T00:00:00Z"),
                              (["2026-08"], "2026-10-08"),
                              (["2026-08"], "2026-10-08T00:00:00")):
            with self.subTest(mask=mask, instant=instant):
                with self.assertRaises(component.CommodityComponentError):
                    component.normalize_commodity_component(verified, SCHEMA, mask, instant)

    def test_120_month_synthetic_mask_matches_frozen_support_without_runner(self):
        months = tuple(f"{year:04d}-{month:02d}"
                       for year in range(2016, 2027) for month in range(1, 13)
                       if "2016-09" <= f"{year:04d}-{month:02d}" <= "2026-08")
        _, candidate = self.candidate(months=months)
        self.assertEqual((len(months), months[0], months[-1]), (120, "2016-09", "2026-08"))
        self.assertEqual(len(candidate["months"]), 240)
        self.assertTrue(all(row["status"] == "complete" for row in candidate["months"]))

    def test_plain_decimal_and_magnitude_bounds(self):
        for token in ("0", "-1", "+1", "1e4", "NaN", "10000000", "0.0000001",
                      "123456789012345678901234567890123", "0." + "1" * 39):
            body = fake_csv(values={("2026-08", "copper"): token})
            with self.subTest(token=token):
                with self.assertRaises(component.CommodityComponentError):
                    self.candidate(body)
                self.archive = self.root / ("fixture-bound-" + sha256(body).hexdigest()[:8])
        _, accepted = self.candidate(fake_csv(values={("2026-08", "copper"): "0.000001"}))
        self.assertEqual(accepted["months"][0]["native_value"], "0.000001")

    def test_fixture_tamper_and_symlink_refuse(self):
        self.fixture()
        original = self.archive / "objects" / "fixture1" / "original.csv"
        original.write_text("changed")
        with self.assertRaises(component.CommodityComponentError):
            component.load_synthetic_component(self.archive, "fixture1", FLAGS)
        original.unlink()
        target = self.root / "target"
        target.write_bytes(fake_csv())
        original.symlink_to(target)
        with self.assertRaises(component.CommodityComponentError):
            component.load_synthetic_component(self.archive, "fixture1", FLAGS)

    def test_nonprivate_fake_original_or_receipt_refuses(self):
        self.fixture()
        original = self.archive / "objects" / "fixture1" / "original.csv"
        original.chmod(0o644)
        with self.assertRaises(component.CommodityComponentError):
            component.load_synthetic_component(self.archive, "fixture1", FLAGS)
        original.chmod(0o600)
        receipt = self.archive / "committed" / "fixture1.json"
        receipt.chmod(0o644)
        with self.assertRaises(component.CommodityComponentError):
            component.load_synthetic_component(self.archive, "fixture1", FLAGS)

    def test_normalizer_rejects_caller_replaced_body_before_candidate(self):
        verified = self.fixture()
        forged = {**verified, "original_body": fake_csv(values={("2026-08", "copper"): "3000"})}
        with self.assertRaisesRegex(component.CommodityComponentError, "SOURCE_CHANGED"):
            component.normalize_commodity_component(forged, SCHEMA, ["2026-08"],
                                                    "2026-10-08T00:00:00Z")

    def test_same_bytes_replaced_inode_during_original_read_refuses(self):
        self.fixture()
        source = self.archive / "objects" / "fixture1" / "original.csv"
        replacement = source.with_name("replacement.csv")
        replacement.write_bytes(source.read_bytes())
        file_open = component._file
        replaced = False
        def replace_after_open(fd, name):
            nonlocal replaced
            opened = file_open(fd, name)
            if name == "original.csv" and not replaced:
                replaced = True
                os.replace(replacement, source)
            return opened
        with patch.object(component, "_file", side_effect=replace_after_open):
            with self.assertRaises(component.CommodityComponentError):
                component.load_synthetic_component(self.archive, "fixture1", FLAGS)
        self.assertTrue(replaced)

    def test_complete_publication_rehash_no_clobber_and_tamper(self):
        verified, candidate = self.candidate()
        receipt = component.publish_commodity_component(candidate, verified, self.output, "run")
        self.assertEqual(receipt["status"], "SYNTHETIC_OFFLINE_ONLY")
        self.assertEqual(component.verify_commodity_component(self.output, "run", verified, SCHEMA,
                                                               ["2026-08"], "2026-10-08T00:00:00Z"), receipt)
        with self.assertRaises(component.CommodityComponentError):
            component.publish_commodity_component(candidate, verified, self.output, "run")
        panel = self.output / "objects" / "run" / "panel.csv"
        panel.write_bytes(panel.read_bytes() + b"tamper")
        with self.assertRaises(component.CommodityComponentError):
            component.verify_commodity_component(self.output, "run", verified, SCHEMA,
                                                 ["2026-08"], "2026-10-08T00:00:00Z")

    def test_incomplete_month_has_no_complete_marker(self):
        verified, candidate = self.candidate(fake_csv(values={("2026-08", "copper"): None}))
        receipt = component.publish_commodity_component(candidate, verified, self.output, "run")
        self.assertEqual(receipt["status"], "SYNTHETIC_OFFLINE_INCOMPLETE")
        self.assertFalse((self.output / "complete" / "run.json").exists())
        with self.assertRaises(component.CommodityComponentError):
            component.verify_commodity_component(self.output, "run", verified, SCHEMA,
                                                 ["2026-08"], "2026-10-08T00:00:00Z")

    def test_postlink_failure_disqualifies_without_unlink(self):
        verified, candidate = self.candidate()
        original = component.verify_commodity_component
        def fail(*args, **kwargs):
            raise component.CommodityComponentError("PUBLICATION_INVALID")
        with patch.object(component, "verify_commodity_component", side_effect=fail):
            with self.assertRaises(component.CommodityComponentError):
                component.publish_commodity_component(candidate, verified, self.output, "run")
        self.assertTrue((self.output / "complete" / "run.json").exists())
        self.assertTrue((self.output / "incomplete" / "run.json").exists())
        with self.assertRaises(component.CommodityComponentError):
            original(self.output, "run", verified, SCHEMA, ["2026-08"], "2026-10-08T00:00:00Z")

    def test_foreign_replacement_marker_preserved(self):
        verified, candidate = self.candidate()
        foreign = b'{"foreign":true}'
        def fail(*args, **kwargs):
            marker = self.output / "complete" / "run.json"
            marker.unlink()
            marker.write_bytes(foreign)
            raise component.CommodityComponentError("PUBLICATION_INVALID")
        with patch.object(component, "verify_commodity_component", side_effect=fail):
            with self.assertRaises(component.CommodityComponentError):
                component.publish_commodity_component(candidate, verified, self.output, "run")
        self.assertEqual((self.output / "complete" / "run.json").read_bytes(), foreign)
        self.assertTrue((self.output / "incomplete" / "run.json").exists())

    def test_verifier_rechecks_incomplete_marker_added_during_read(self):
        verified, candidate = self.candidate()
        component.publish_commodity_component(candidate, verified, self.output, "run")
        read = component._read_bound
        added = False
        def race(fd, name, maximum):
            nonlocal added
            result = read(fd, name, maximum)
            if name == "run.json" and not added:
                added = True
                (self.output / "incomplete" / "run.json").write_bytes(b'{"status":"incomplete"}')
            return result
        with patch.object(component, "_read_bound", side_effect=race):
            with self.assertRaisesRegex(component.CommodityComponentError, "PUBLICATION_INCOMPLETE"):
                component.verify_commodity_component(self.output, "run", verified, SCHEMA,
                                                     ["2026-08"], "2026-10-08T00:00:00Z")

    def test_postlink_marker_close_failure_is_disqualified(self):
        verified, candidate = self.candidate()
        original_directory = component._directory
        original_close = os.close
        complete_opens = 0
        marker_fd = None
        failed = False
        def track_directory(parent_fd, name):
            nonlocal complete_opens, marker_fd
            fd = original_directory(parent_fd, name)
            if name == "complete":
                complete_opens += 1
                if complete_opens == 2:
                    marker_fd = fd
            return fd
        def fail_close(fd):
            nonlocal failed
            original_close(fd)
            if fd == marker_fd and not failed:
                failed = True
                raise OSError("synthetic close failure")
        with patch.object(component, "_directory", side_effect=track_directory), \
             patch.object(component.os, "close", side_effect=fail_close):
            with self.assertRaises(component.CommodityComponentError):
                component.publish_commodity_component(candidate, verified, self.output, "run")
        self.assertTrue(failed)
        self.assertTrue((self.output / "complete" / "run.json").exists())
        self.assertTrue((self.output / "incomplete" / "run.json").exists())

    def test_postlink_root_close_failure_is_disqualified(self):
        verified, candidate = self.candidate()
        layout = component._ensure_layout
        close = os.close
        root_fd = None
        failed = False
        def track_layout(path):
            nonlocal root_fd
            root_fd = layout(path)
            return root_fd
        def fail_close(fd):
            nonlocal failed
            close(fd)
            if fd == root_fd and not failed:
                failed = True
                raise OSError("synthetic root close failure")
        with patch.object(component, "_ensure_layout", side_effect=track_layout), \
             patch.object(component.os, "close", side_effect=fail_close):
            with self.assertRaises(component.CommodityComponentError):
                component.publish_commodity_component(candidate, verified, self.output, "run")
        self.assertTrue(failed)
        self.assertTrue((self.output / "complete" / "run.json").exists())
        self.assertTrue((self.output / "incomplete" / "run.json").exists())

    def test_disqualified_postlink_close_error_keeps_incomplete_priority(self):
        verified, candidate = self.candidate()
        directory = component._directory
        close = os.close
        count = 0
        marker_fd = None
        def track(fd, name):
            nonlocal count, marker_fd
            opened = directory(fd, name)
            if name == "complete":
                count += 1
                if count == 2:
                    marker_fd = opened
            return opened
        def fail_close(fd):
            close(fd)
            if fd == marker_fd:
                raise OSError("synthetic close failure after disqualification")
        with patch.object(component, "_directory", side_effect=track), \
             patch.object(component.os, "close", side_effect=fail_close), \
             patch.object(component, "verify_commodity_component",
                          side_effect=component.CommodityComponentError("PUBLICATION_INVALID")):
            with self.assertRaisesRegex(component.CommodityComponentError,
                                        "PUBLICATION_INCOMPLETE"):
                component.publish_commodity_component(candidate, verified, self.output, "run")
        self.assertTrue((self.output / "incomplete" / "run.json").exists())

    def test_prelink_staged_tamper_never_publishes_complete(self):
        verified, candidate = self.candidate()
        real_verify = component._verify
        def tamper_then_verify(*args, **kwargs):
            panel = self.output / "objects" / "run" / "panel.csv"
            panel.write_bytes(panel.read_bytes() + b"tamper")
            return real_verify(*args, **kwargs)
        with patch.object(component, "_verify", side_effect=tamper_then_verify):
            with self.assertRaises(component.CommodityComponentError):
                component.publish_commodity_component(candidate, verified, self.output, "run")
        self.assertFalse((self.output / "complete" / "run.json").exists())

    def test_disqualifier_write_failure_is_fatal_and_preserves_complete_marker(self):
        verified, candidate = self.candidate()
        write = component._write_new
        def fail_disqualifier(fd, name, body):
            if name == "run.json":
                raise PermissionError("synthetic denied")
            return write(fd, name, body)
        with patch.object(component, "verify_commodity_component",
                          side_effect=component.CommodityComponentError("PUBLICATION_INVALID")), \
             patch.object(component, "_write_new", side_effect=fail_disqualifier):
            with self.assertRaisesRegex(component.CommodityComponentError,
                                        "PUBLICATION_CLEANUP_UNRESOLVED"):
                component.publish_commodity_component(candidate, verified, self.output, "run")
        self.assertTrue((self.output / "complete" / "run.json").exists())

    def test_preexisting_foreign_incomplete_marker_is_preserved(self):
        verified, candidate = self.candidate()
        foreign = b'{"foreign":true}'
        def insert_foreign(*args, **kwargs):
            (self.output / "incomplete" / "run.json").write_bytes(foreign)
            raise component.CommodityComponentError("PUBLICATION_INVALID")
        with patch.object(component, "verify_commodity_component", side_effect=insert_foreign):
            with self.assertRaisesRegex(component.CommodityComponentError,
                                        "PUBLICATION_CLEANUP_UNRESOLVED"):
                component.publish_commodity_component(candidate, verified, self.output, "run")
        self.assertEqual((self.output / "incomplete" / "run.json").read_bytes(), foreign)


if __name__ == "__main__":
    unittest.main()
