"""New offline interactions only; hand-picked fake bytes, no acquisition or models."""
from __future__ import annotations
import copy
import csv
from decimal import getcontext, localcontext
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from procure.evaluation import commodity_component as commodity
from procure.evaluation import fx_normalization as fx
from procure.evaluation import commodity_fx_join as join

FLAGS = {"synthetic": True, "source_authenticity": False, "observed_eligible": False,
         "execution_authorized": False, "rights": "synthetic_only"}
MONTHS = ["2026-07", "2026-08"]
ASOF = "2026-10-08T00:00:00.000000Z"


def canon(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def digest(x):
    return hashlib.sha256(x).hexdigest()


def csv_bytes(header, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode()


def private_file(path, body, mode=0o600):
    path.write_bytes(body)
    path.chmod(mode)


def dirs(root, run):
    for p in (root, root / "objects", root / "objects" / run, root / "committed"):
        p.mkdir(mode=0o700, exist_ok=True)
        p.chmod(0o700)


class JoinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-commodity-fx-join-")
        self.root = Path(self.tmp.name).resolve()
        self.serial = 0

    def tearDown(self):
        self.tmp.cleanup()

    def fixtures(self, missing=False, absent=None, asof=ASOF, commodity_asof=None):
        self.serial += 1
        home = self.root / str(self.serial)
        home.mkdir(mode=0o700)
        cr, fr = home / "commodity-original", home / "fx-original"
        cp, fp = home / "commodity-panel", home / "fx-panel"
        run = "fixture"
        dirs(cr, run)
        rows = []
        for month in MONTHS:
            for series in ("copper", "aluminum"):
                value = "1500" if series == "aluminum" else ("2000" if month == "2026-07" else "1000")
                is_missing = missing and (month, series) == ("2026-08", "copper")
                rows.append([month, series, "USD/metric_tonne", "M" if is_missing else "A", "" if is_missing else value])
        body = csv_bytes(["month", "series_id", "unit", "status", "value"], rows)
        receipt = {"schema_version": "synthetic-commodity-fixture-receipt.v1", **FLAGS,
                   "run_id": run, "first_month": MONTHS[0], "last_month": MONTHS[-1],
                   "month_count": 2, "original_bytes": len(body), "original_sha256": digest(body)}
        private_file(cr / "objects" / run / "original.csv", body)
        private_file(cr / "committed" / (run + ".json"), canon(receipt))
        cv = commodity.load_synthetic_component(cr, run, FLAGS)
        ca = commodity.normalize_commodity_component(cv, commodity.SCHEMA, MONTHS, commodity_asof or asof)
        cm = commodity.publish_commodity_component(ca, cv, cp, "panel")
        dirs(fr, run)
        (fr / "incomplete").mkdir(mode=0o700)
        originals = []
        dates = ["2026-07-01", "2026-07-02", "2026-08-03", "2026-08-04"]
        for sid, currency, values in (("ecb_usd_eur", "USD", ["2", "4", "3", "3"]),
                                      ("ecb_inr_eur", "INR", ["100", "120", "1", "1"])):
            data = csv_bytes(fx.HEADERS, [[f"D.{currency}.EUR.SP00.A", "D", currency,
                              "EUR", "SP00", "A", day, val, "SYNTHETIC_QUOTE", "0", "A"]
                              for day, val in zip(dates, values)
                              if not (absent and day == "2026-08-04" and sid in absent)])
            private_file(fr / "objects" / run / (sid + ".raw"), data, 0o400)
            originals.append({"source_id": sid, "path": f"objects/{run}/{sid}.raw",
                              "bytes": len(data), "sha256": digest(data)})
        sr = {"schema_version": "offline-source-publication.v1", "run_id": run,
              "status": "OFFLINE_COMPLETE", "offline_only": True, "execution_authorized": False,
              "originals": sorted(originals, key=lambda r: r["source_id"]),
              "settled": True, "capture_verified": True}
        private_file(fr / "objects" / run / "manifest.json", canon(sr), 0o400)
        os.link(fr / "objects" / run / "manifest.json", fr / "committed" / (run + ".json"))
        (fr / "objects" / run).chmod(0o500)
        provenance = {**FLAGS, "retrieved_at": ASOF}
        coverage = {"schema_version": "synthetic-date-mask.v2", "synthetic": True,
                    "as_of": asof, "required_months": MONTHS.copy(),
                    "dates_by_month": {"2026-07": dates[:2], "2026-08": dates[2:]}}
        fv = fx.load_verified_originals(fr, run, provenance)
        fa = fx.normalize_ecb_pair(fv, fx.SCHEMA, coverage)
        fm = fx.publish_fx_panel(fa, provenance, fp, "panel")
        def panel_bind(root, manifest):
            raw = (root / "objects" / "panel" / "manifest.json").read_bytes()
            return {"panel_root": str(root), "panel_run_id": "panel", "panel_manifest_sha256": digest(raw),
                    "panel_sha256": manifest["panel_sha256"], "panel_bytes": manifest["panel_bytes"],
                    "schema_sha256": manifest["schema_sha256"], "code_sha256": manifest["code_sha256"]}
        cb = {**panel_bind(cp, cm), "original_root": str(cr), "fixture_run_id": run,
              "fixture_receipt_sha256": cv["receipt_sha256"], "original_sha256": cv["original_sha256"],
              "original_bytes": cv["original_bytes"]}
        fb = {**panel_bind(fp, fm), "original_root": str(fr), "source_run_id": run,
              "provenance": provenance, "source_receipt_sha256": fv["receipt_sha256"],
              "source_originals": fm["source_originals"], "coverage_sha256": fm["coverage_sha256"],
              "coverage": coverage}
        policy = {"schema_version": "synthetic-commodity-fx-join.v1", **FLAGS,
                  "series_ids": ["copper", "aluminum"], "required_months": MONTHS.copy(),
                  "as_of": asof, "commodity_schema_sha256": digest(canon(commodity.SCHEMA)),
                  "fx_schema_sha256": digest(canon(fx.SCHEMA)), "fx_coverage_sha256": digest(canon(coverage)),
                  "arithmetic_version": "component_fraction_product_v1",
                  "rounding_version": "decimal50_half_even_normalized_plain_v1"}
        return {"commodity": cb, "fx": fb}, policy

    def candidate(self, **kw):
        b, p = self.fixtures(**kw)
        return b, p, join.compose_joined_panel(join.load_join_inputs(b, p), p)

    def test_daily_cross_and_exact_products_not_rounded_operands(self):
        _, _, out = self.candidate()
        rows = out["rows"]
        self.assertEqual([r["joined"]["value"] for r in rows], ["80", "60", "0." + "3" * 50, "0.5"])
        self.assertEqual([r["joined"]["basis"] for r in rows],
                         [{"numerator": "80", "denominator": "1"}, {"numerator": "60", "denominator": "1"},
                          {"numerator": "1", "denominator": "3"}, {"numerator": "1", "denominator": "2"}])
        self.assertEqual(rows[0]["fx"]["basis"], {"numerator": "40", "denominator": "1"})
        self.assertEqual(out["status"], "SYNTHETIC_OFFLINE_ONLY")
        self.assertFalse(out["observed_eligible"])
        self.assertFalse(out["execution_authorized"])

    def test_complete_publication_whole_result_and_no_clobber(self):
        b, p, out = self.candidate()
        root = self.root / "joined"
        receipt = join.publish_joined_panel(out, b, p, root, "joined")
        self.assertEqual(receipt, join.verify_joined_panel(root, "joined", b, p))
        self.assertEqual(json.loads((root / "objects/joined/panel.json").read_bytes()), out)
        before = (root / "complete/joined.json").read_bytes()
        with self.assertRaisesRegex(join.JoinError, "PUBLICATION_EXISTS"):
            join.publish_joined_panel(out, b, p, root, "joined")
        self.assertEqual((root / "complete/joined.json").read_bytes(), before)

    def test_source_missing_retains_component_status_and_diagnostic_only(self):
        b, p, out = self.candidate(missing=True)
        self.assertEqual(len(out["rows"]), 4)
        r = out["rows"][2]
        self.assertEqual(r["commodity"]["reason"], "source_missing")
        self.assertEqual(r["fx"]["status"], "complete")
        self.assertIsNone(r["joined"]["basis"])
        self.assertIsNone(r["joined"]["value"])
        self.assertEqual(r["joined"]["reasons"], ["commodity:source_missing"])
        self.assertEqual(out["rows"][3]["joined"]["value"], "0.5")
        root = self.root / "incomplete-joined"
        receipt = join.publish_joined_panel(out, b, p, root, "missing")
        self.assertEqual(receipt, join.inspect_joined_panel(root, "missing", b, p))
        with self.assertRaisesRegex(join.JoinError, "PUBLICATION_INCOMPLETE"):
            join.verify_joined_panel(root, "missing", b, p)

    def test_shared_and_one_source_missing_dates_never_shorten_mean(self):
        for absent in (("ecb_usd_eur",), ("ecb_usd_eur", "ecb_inr_eur")):
            with self.subTest(absent=absent):
                _, _, out = self.candidate(absent=absent)
                self.assertEqual(out["rows"][0]["joined"]["value"], "80")
                for r in out["rows"][2:]:
                    self.assertIsNone(r["joined"]["value"])
                    self.assertIn("fx:EXPECTED_DATE_MISSING", r["joined"]["reasons"])

    def test_full_microsecond_boundary_and_equivalent_offset(self):
        for instant, expected in (("2026-08-31T23:59:59.999998Z", "unavailable"),
                                   ("2026-08-31T23:59:59.999999Z", "complete")):
            _, _, out = self.candidate(asof=instant)
            self.assertEqual(out["rows"][0]["joined"]["status"], expected)
        _, _, out = self.candidate(commodity_asof="2026-10-08T05:30:00.000000+05:30")
        self.assertEqual(out["rows"][0]["joined"]["value"], "80")
        b, p = self.fixtures(commodity_asof="2026-10-08T00:00:00.000001Z")
        with self.assertRaisesRegex(join.JoinError, "AS_OF_MISMATCH"):
            join.load_join_inputs(b, p)

    def test_observed_and_iron_refuse_before_open(self):
        b, p = self.fixtures()
        for field, value in (("synthetic", False), ("source_authenticity", True),
                              ("observed_eligible", True), ("execution_authorized", True),
                              ("rights", "APPROVED")):
            bad = {**p, field: value}
            with patch.object(join.os, "open", side_effect=AssertionError("unsafe open")):
                with self.assertRaisesRegex(join.JoinError, "OBSERVED_HOLD"):
                    join.load_join_inputs(b, bad)
        with self.assertRaisesRegex(join.JoinError, "UNIT_HOLD"):
            join.load_join_inputs(b, {**p, "series_ids": ["iron_ore"]})

    def test_mask_hash_size_and_strict_types_are_not_caller_authority(self):
        b, p = self.fixtures()
        alterations = [("required_months", ["2026-08"]), ("required_months", []),
                       ("as_of", "2026-10-08"), ("fx_coverage_sha256", "0" * 64),
                       ("synthetic", 1)]
        for key, value in alterations:
            with self.subTest(key=key, value=value):
                with self.assertRaises(join.JoinError):join.load_join_inputs(b, {**p, key: value})
        for side, key, value in (("commodity", "original_bytes", True),
                                 ("fx", "panel_bytes", True), ("commodity", "code_sha256", "0" * 64)):
            bad = copy.deepcopy(b); bad[side][key] = value
            with self.assertRaises(join.JoinError):join.load_join_inputs(bad, p)

    def test_ambient_decimal_and_snapshot_mutation_do_not_change_output(self):
        b, p = self.fixtures()
        snapshot = join.load_join_inputs(b, p)
        before = join.compose_joined_panel(snapshot, p)
        b["commodity"]["original_sha256"] = "0" * 64
        with localcontext() as context:
            context.prec = 3
            context.rounding = "ROUND_UP"
            self.assertEqual(join.compose_joined_panel(snapshot, p), before)

    def test_failure_disqualifier_not_natural_incomplete_and_no_demotion(self):
        b, p = self.fixtures(missing=True)
        root = Path(b["commodity"]["panel_root"])
        marker = root / "incomplete/panel.json"
        marker.unlink()  # Fixture adversary only, never production cleanup.
        private_file(marker, canon({"status": "SYNTHETIC_OFFLINE_INCOMPLETE", "reason": "POSTLINK_FINALIZATION_FAILED"}))
        with self.assertRaisesRegex(join.JoinError, "COMPONENT"):
            join.load_join_inputs(b, p)
        b, p = self.fixtures()
        with patch.object(commodity, "verify_commodity_component", side_effect=commodity.CommodityComponentError("PUBLICATION_INVALID")):
            with self.assertRaisesRegex(join.JoinError, "COMPONENT"):
                join.load_join_inputs(b, p)

    def test_symlink_ancestor_and_public_root_refuse(self):
        b, p = self.fixtures()
        alias = self.root / "alias"
        alias.symlink_to(Path(b["commodity"]["original_root"]).parent, target_is_directory=True)
        b["commodity"]["original_root"] = str(alias / "commodity-original")
        with self.assertRaises(join.JoinError):join.load_join_inputs(b, p)
        b, p = self.fixtures()
        Path(b["commodity"]["panel_root"]).chmod(0o755)
        with self.assertRaises(join.JoinError):join.load_join_inputs(b, p)

    def test_candidate_changes_cannot_publish(self):
        b, p, out = self.candidate()
        out["rows"][0]["joined"]["value"] = "81"
        root = self.root / "must-not-exist"
        with self.assertRaises(join.JoinError):join.publish_joined_panel(out, b, p, root, "bad")
        self.assertFalse(root.exists())

    def test_late_incomplete_during_verifier_close_disqualifies(self):
        b, p, out = self.candidate()
        root = self.root / "late-verifier"
        join.publish_joined_panel(out, b, p, root, "run")
        inode = root.stat().st_ino
        real_close = os.close
        injected = []
        foreign = b'{"foreign":"late incomplete"}'
        def close(fd):
            target = os.fstat(fd).st_ino == inode
            real_close(fd)
            if target and not injected:
                private_file(root / "incomplete/run.json", foreign)
                injected.append(True)
        with patch.object(join.os, "close", side_effect=close):
            with self.assertRaises(join.JoinError):
                join.verify_joined_panel(root, "run", b, p)
        self.assertEqual((root / "incomplete/run.json").read_bytes(), foreign)

    def test_late_incomplete_during_publisher_close_disqualifies(self):
        b, p, out = self.candidate()
        root = self.root / "late-publisher"
        real_close = os.close
        closes = []
        foreign = b'{"foreign":"publisher close"}'
        def close(fd):
            target = root.exists() and os.fstat(fd).st_ino == root.stat().st_ino
            real_close(fd)
            if target:
                closes.append(True)
                if len(closes) == 3:private_file(root / "incomplete/run.json", foreign)
        with patch.object(join.os, "close", side_effect=close):
            with self.assertRaises(join.JoinError):join.publish_joined_panel(out, b, p, root, "run")
        self.assertGreaterEqual(len(closes), 3)  # Recovery closes the same root too.
        self.assertEqual((root / "incomplete/run.json").read_bytes(), foreign)

    def test_disqualifier_creation_inode_not_replacement_after_close(self):
        b, p, out = self.candidate()
        root = self.root / "foreign-disqualifier"
        real_close, real_sync = os.close, os.fsync
        replaced, failed = [], []
        foreign_inode = []
        def sync(fd):
            positive = root / "complete"
            if positive.exists() and os.fstat(fd).st_ino == positive.stat().st_ino and not failed:
                failed.append(True)
                raise OSError("injected final marker fsync failure")
            real_sync(fd)
        def close(fd):
            marker = root / "incomplete/run.json"
            target = marker.exists() and os.fstat(fd).st_ino == marker.stat().st_ino
            real_close(fd)
            if target and not replaced:
                body = marker.read_bytes()
                marker.rename(root / "incomplete/retained-created.json")
                private_file(marker, body)
                foreign_inode.append(marker.stat().st_ino)
                replaced.append(True)
        with patch.object(join.os, "fsync", side_effect=sync), patch.object(join.os, "close", side_effect=close):
            with self.assertRaisesRegex(join.JoinError, "PUBLICATION_CLEANUP_UNRESOLVED"):
                join.publish_joined_panel(out, b, p, root, "run")
        self.assertEqual((root / "incomplete/run.json").stat().st_ino, foreign_inode[0])
        self.assertEqual((root / "incomplete/run.json").read_bytes(),
                         (root / "incomplete/retained-created.json").read_bytes())

    def test_same_byte_original_and_directory_replacement_refuse(self):
        for directory in (False, True):
            b, p = self.fixtures()
            root = Path(b["commodity"]["original_root"])
            path = root / "objects/fixture/original.csv"
            real_verify = commodity.verify_commodity_component
            foreign = []
            def verify(*args, **kwargs):
                result = real_verify(*args, **kwargs)
                if directory:
                    old = root.with_name("retained-original-root")
                    root.rename(old); shutil.copytree(old, root)
                    foreign.append(root.stat().st_ino)
                else:
                    raw = path.read_bytes()
                    path.rename(path.with_name("retained-original.csv"))
                    private_file(path, raw)
                    foreign.append(path.stat().st_ino)
                return result
            with patch.object(commodity, "verify_commodity_component", side_effect=verify):
                with self.assertRaises(join.JoinError):join.load_join_inputs(b, p)
            self.assertEqual((root if directory else path).stat().st_ino, foreign[0])

    def test_same_byte_component_marker_replacement_refuses(self):
        b, p = self.fixtures()
        marker = Path(b["fx"]["panel_root"]) / "complete/panel.json"
        raw = marker.read_bytes()
        real_verify = fx.verify_fx_panel
        foreign = []
        def verify(*args, **kwargs):
            result = real_verify(*args, **kwargs)
            marker.rename(marker.with_name("retained.json"))
            private_file(marker, raw)
            foreign.append(marker.stat().st_ino)
            return result
        with patch.object(fx, "verify_fx_panel", side_effect=verify):
            with self.assertRaises(join.JoinError):join.load_join_inputs(b, p)
        self.assertEqual(marker.stat().st_ino, foreign[0])
        self.assertEqual(marker.read_bytes(), raw)

    def test_natural_incomplete_plus_dangling_complete_refuses(self):
        b, p = self.fixtures(missing=True)
        marker = Path(b["commodity"]["panel_root"]) / "complete/panel.json"
        marker.symlink_to("absent-foreign-target")
        with self.assertRaisesRegex(join.JoinError, "COMPONENT_DISQUALIFIED"):
            join.load_join_inputs(b, p)
        self.assertTrue(marker.is_symlink())

    def test_final_fx_disqualifier_and_same_byte_incomplete_replacement_refuse(self):
        b, p = self.fixtures()
        marker = Path(b["fx"]["panel_root"]) / "incomplete/panel.json"
        real_verify = fx.verify_fx_panel
        def verify(*args, **kwargs):
            result = real_verify(*args, **kwargs)
            private_file(marker, b'{"foreign":"late FX"}')
            return result
        with patch.object(fx, "verify_fx_panel", side_effect=verify):
            with self.assertRaises(join.JoinError):join.load_join_inputs(b, p)
        self.assertEqual(marker.read_bytes(), b'{"foreign":"late FX"}')
        b, p = self.fixtures(missing=True)
        marker = Path(b["commodity"]["panel_root"]) / "incomplete/panel.json"
        raw = marker.read_bytes()
        real_load = commodity.load_synthetic_component
        changed = []
        def load(*args, **kwargs):
            result = real_load(*args, **kwargs)
            if not changed:
                marker.rename(marker.with_name("retained.json")); private_file(marker, raw)
                changed.append(marker.stat().st_ino)
            return result
        with patch.object(commodity, "load_synthetic_component", side_effect=load):
            with self.assertRaises(join.JoinError):join.load_join_inputs(b, p)
        self.assertEqual(marker.stat().st_ino, changed[0])

    def test_half_even_ties_fraction_bounds_and_nonreduced_basis(self):
        self.assertEqual(join._render(Fraction(10**50 + 5, 10**50)), "1")
        self.assertEqual(join._render(Fraction(10**50 + 15, 10**50)), "1." + "0" * 48 + "2")
        for basis in ({"numerator": "2", "denominator": "4"}, {"numerator": "1", "denominator": "0"},
                      {"numerator": "1e3", "denominator": "1"}, {"numerator": True, "denominator": "1"}):
            with self.assertRaises(join.JoinError):join._fraction(basis)
        with self.assertRaisesRegex(join.JoinError, "ARITHMETIC_LIMIT"):
            join._render(Fraction(1 << 4096, 1))

    def test_postlink_fsync_failure_retains_complete_without_any_marker_delete(self):
        b, p, out = self.candidate()
        root = self.root / "sync-failure"
        real_sync = os.fsync
        failed = []
        def sync(fd):
            directory = root / "complete"
            if directory.exists() and os.fstat(fd).st_ino == directory.stat().st_ino and not failed:
                failed.append(True); raise OSError("injected marker fsync")
            real_sync(fd)
        with patch.object(join.os, "fsync", side_effect=sync), \
             patch.object(join.os, "unlink", side_effect=AssertionError("marker deletion")), \
             patch.object(join.os, "rename", side_effect=AssertionError("marker rename")), \
             patch.object(join.os, "replace", side_effect=AssertionError("marker replace")):
            with self.assertRaisesRegex(join.JoinError, "PUBLICATION_INCOMPLETE"):
                join.publish_joined_panel(out, b, p, root, "run")
        self.assertTrue((root / "complete/run.json").exists())
        negative = json.loads((root / "incomplete/run.json").read_bytes())
        self.assertEqual(negative["reason"], "POSTLINK_FINALIZATION_FAILED")
        with self.assertRaises(join.JoinError):join.verify_joined_panel(root, "run", b, p)
        with self.assertRaises(join.JoinError):join.inspect_joined_panel(root, "run", b, p)

    def test_prelink_failure_never_publishes_marker(self):
        b, p, out = self.candidate()
        root = self.root / "prelink"
        real_sync = os.fsync
        def sync(fd):
            obj = root / "objects/run"
            if obj.exists() and os.fstat(fd).st_ino == obj.stat().st_ino:
                raise OSError("injected staged directory fsync")
            real_sync(fd)
        with patch.object(join.os, "fsync", side_effect=sync):
            with self.assertRaises(join.JoinError):join.publish_joined_panel(out, b, p, root, "run")
        self.assertFalse((root / "complete/run.json").exists())
        self.assertFalse((root / "incomplete/run.json").exists())
        self.assertTrue((root / "objects/run").exists())

    def test_replaced_root_recovery_never_writes_foreign_root(self):
        b, p, out = self.candidate()
        root = self.root / "replaced-root"
        retained = self.root / "retained-joined-root"
        real_close = os.close
        closes = []
        def close(fd):
            target = root.exists() and os.fstat(fd).st_ino == root.stat().st_ino
            real_close(fd)
            if target:
                closes.append(True)
                if len(closes) == 3:
                    root.rename(retained); root.mkdir(mode=0o700)
                    (root / "incomplete").mkdir(mode=0o700)
                    private_file(root / "foreign.txt", b"foreign root preserved")
        with patch.object(join.os, "close", side_effect=close):
            with self.assertRaisesRegex(join.JoinError, "PUBLICATION_CLEANUP_UNRESOLVED"):
                join.publish_joined_panel(out, b, p, root, "run")
        self.assertEqual((root / "foreign.txt").read_bytes(), b"foreign root preserved")
        self.assertFalse((root / "incomplete/run.json").exists())
        self.assertTrue((retained / "complete/run.json").exists())

    def test_pure_composition_rejects_empty_duplicate_and_wrong_unit_snapshots(self):
        b, p = self.fixtures()
        snapshot = join.load_join_inputs(b, p)
        for mutation in ("empty", "duplicate", "unit", "status"):
            payload = json.loads(snapshot.payload)
            if mutation == "empty":payload["rows"] = []
            elif mutation == "duplicate":payload["rows"][1] = copy.deepcopy(payload["rows"][0])
            elif mutation == "unit":payload["rows"][0]["fx"]["unit"] = "INR/EUR"
            else:payload["component_statuses"]["commodity_USD"] = "SYNTHETIC_OFFLINE_INCOMPLETE"
            forged = join.VerifiedJoinInputs(snapshot.policy, canon(payload))
            with self.subTest(mutation=mutation):
                with self.assertRaises(join.JoinError):join.compose_joined_panel(forged, p)

    def test_nonregular_original_refuses_without_waiting_for_fifo_writer(self):
        b, p = self.fixtures()
        path = Path(b["commodity"]["original_root"]) / "objects/fixture/original.csv"
        path.rename(path.with_name("retained.csv"))
        os.mkfifo(path, 0o600)
        done = threading.Event()
        caught = []
        def read():
            try:join.load_join_inputs(b, p)
            except BaseException as error:caught.append(error)
            finally:done.set()
        reader = threading.Thread(target=read)
        reader.start()
        refused_without_writer = done.wait(1)
        if not refused_without_writer:
            # Unblock/reap our owned test thread; no leaked blocked reader.
            writer = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            os.close(writer)
        reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertTrue(refused_without_writer, "nonregular source blocked at open")
        self.assertIsInstance(caught[0], join.JoinError)

    def test_observed_receipt_refuses_before_original_body_open(self):
        b, p = self.fixtures()
        receipt = Path(b["commodity"]["original_root"]) / "committed/fixture.json"
        data = json.loads(receipt.read_bytes()); data["synthetic"] = False
        private_file(receipt, canon(data))
        b["commodity"]["fixture_receipt_sha256"] = digest(receipt.read_bytes())
        real_open = os.open
        opened = []
        def open_file(name, flags, *args, **kwargs):
            if name == "original.csv":opened.append(True)
            return real_open(name, flags, *args, **kwargs)
        with patch.object(join.os, "open", side_effect=open_file):
            with self.assertRaises(join.JoinError):join.load_join_inputs(b, p)
        self.assertEqual(opened, [], "observed receipt allowed raw original open")

    def test_invalid_snapshot_nested_reason_is_sanitized(self):
        b, p = self.fixtures()
        snapshot = join.load_join_inputs(b, p)
        payload = json.loads(snapshot.payload)
        payload["rows"][0]["fx"]["reasons"] = [[]]
        caught = None
        try:join.compose_joined_panel(join.VerifiedJoinInputs(snapshot.policy, canon(payload)), p)
        except Exception as error:caught = error
        self.assertIsInstance(caught, join.JoinError)

    def test_disqualification_faults_never_acknowledge_cleanup(self):
        for fault in ("create", "write", "read", "file_sync", "dir_sync", "close"):
            with self.subTest(fault=fault):
                b, p, out = self.candidate()
                root = self.root / ("disqualify-" + fault)
                real_open, real_write, real_read = os.open, os.write, os.read
                real_sync, real_close = os.fsync, os.close
                triggered = []
                marker_failed = []
                def negative_file(fd):
                    path = root / "incomplete/run.json"
                    return path.exists() and os.fstat(fd).st_ino == path.stat().st_ino
                def sync(fd):
                    positive = root / "complete"
                    if positive.exists() and os.fstat(fd).st_ino == positive.stat().st_ino and not marker_failed:
                        marker_failed.append(True); raise OSError("postlink fault")
                    negative = root / "incomplete"
                    if ((fault == "file_sync" and negative_file(fd)) or
                            (fault == "dir_sync" and negative.exists() and os.fstat(fd).st_ino == negative.stat().st_ino)):
                        triggered.append(True); raise OSError("disqualifier fsync fault")
                    real_sync(fd)
                def open_file(name, flags, *args, **kwargs):
                    if fault == "create" and name == "run.json" and flags & os.O_CREAT and marker_failed:
                        triggered.append(True); raise OSError("disqualifier create fault")
                    return real_open(name, flags, *args, **kwargs)
                def write(fd, body):
                    if fault == "write" and negative_file(fd):
                        triggered.append(True); return 0
                    return real_write(fd, body)
                def read(fd, size):
                    if fault == "read" and negative_file(fd):
                        triggered.append(True); raise OSError("disqualifier read fault")
                    return real_read(fd, size)
                def close(fd):
                    target = fault == "close" and negative_file(fd) and not triggered
                    real_close(fd)
                    if target:
                        triggered.append(True); raise OSError("disqualifier close fault")
                with patch.object(join.os, "open", side_effect=open_file), \
                     patch.object(join.os, "write", side_effect=write), \
                     patch.object(join.os, "read", side_effect=read), \
                     patch.object(join.os, "fsync", side_effect=sync), \
                     patch.object(join.os, "close", side_effect=close):
                    with self.assertRaisesRegex(join.JoinError, "PUBLICATION_CLEANUP_UNRESOLVED"):
                        join.publish_joined_panel(out, b, p, root, "run")
                self.assertTrue(triggered)
                self.assertTrue((root / "complete/run.json").exists())

    def test_foreign_complete_after_link_preserved_and_disqualified(self):
        b, p, out = self.candidate()
        root = self.root / "foreign-complete"
        real_link = os.link
        foreign = []
        def link(source, destination, **kwargs):
            real_link(source, destination, **kwargs)
            if destination == "run.json":
                marker = root / "complete/run.json"
                raw = marker.read_bytes()
                marker.rename(marker.with_name("retained-created.json"))
                private_file(marker, raw); foreign.append(marker.stat().st_ino)
        with patch.object(join.os, "link", side_effect=link):
            with self.assertRaisesRegex(join.JoinError, "PUBLICATION_INCOMPLETE"):
                join.publish_joined_panel(out, b, p, root, "run")
        self.assertEqual((root / "complete/run.json").stat().st_ino, foreign[0])
        self.assertEqual((root / "complete/run.json").read_bytes(), (root / "complete/retained-created.json").read_bytes())

    def test_publisher_root_close_failure_disqualifies(self):
        b, p, out = self.candidate()
        root = self.root / "close-failure"
        real_close = os.close
        closes = []
        def close(fd):
            target = root.exists() and os.fstat(fd).st_ino == root.stat().st_ino
            real_close(fd)
            if target:
                closes.append(True)
                if len(closes) == 3:raise OSError("owned root close fault")
        with patch.object(join.os, "close", side_effect=close):
            with self.assertRaisesRegex(join.JoinError, "PUBLICATION_INCOMPLETE"):
                join.publish_joined_panel(out, b, p, root, "run")
        self.assertTrue((root / "complete/run.json").exists())
        self.assertEqual(json.loads((root / "incomplete/run.json").read_bytes())["reason"], "POSTLINK_FINALIZATION_FAILED")

    def test_whole_manifest_matches_independent_contract_projection(self):
        b, p, out = self.candidate()
        identities = {}
        for kind in ("commodity", "fx"):
            binding = b[kind]
            raw = (Path(binding["panel_root"]) / "objects/panel/manifest.json").read_bytes()
            m = json.loads(raw)
            common = ["panel_sha256", "panel_bytes", "schema_sha256", "code_sha256",
                      "arithmetic_version", "availability_basis", "rights"]
            keys = (common + ["fixture_run_id", "fixture_receipt_sha256", "original_sha256", "original_bytes", "mask_sha256", "as_of"]
                    if kind == "commodity" else common + ["source_run_id", "source_receipt_sha256", "source_originals", "coverage_sha256", "rounding_version", "synthetic_fixture_timestamp"])
            identity = {key: m[key] for key in keys}
            identity.update(panel_run_id="panel", panel_manifest_sha256=digest(raw), panel_manifest_bytes=len(raw))
            if kind == "fx":identity["as_of"] = ASOF
            identities[kind] = identity
        module_root = Path(join.__file__).parent
        codes = {key: digest((module_root / filename).read_bytes())
                 for key, filename in (("commodity", "commodity_component.py"), ("fx", "fx_normalization.py"),
                                       ("transport", "source_transport.py"), ("join", "commodity_fx_join.py"))}
        expected = {"schema_version": "synthetic-commodity-fx-join-receipt.v1", **FLAGS,
                    "run_id": "literal", "status": "SYNTHETIC_OFFLINE_ONLY",
                    "policy_sha256": digest(canon(p)), "mask_sha256": digest(canon(MONTHS)),
                    "as_of": ASOF, "month_count": 2, "row_count": 4, "component_identities": identities,
                    "code_identities": codes, "panel_sha256": digest(canon(out)), "panel_bytes": len(canon(out)),
                    "availability_basis": "end_of_following_month_utc_proxy_not_pit",
                    "arithmetic_version": "component_fraction_product_v1",
                    "rounding_version": "decimal50_half_even_normalized_plain_v1"}
        root = self.root / "independent-whole"
        self.assertEqual(join.publish_joined_panel(out, b, p, root, "literal"), expected)
        self.assertEqual(join.verify_joined_panel(root, "literal", b, p), expected)
        self.assertEqual((root / "objects/literal/manifest.json").read_bytes(), canon(expected))

    def test_invalid_fx_tokens_and_metadata_are_refused_through_actual_join_path(self):
        for token in ("0", "-1", "NaN", "1e3", "9" * 65, "wrong_currency", "wrong_unit"):
            b, p = self.fixtures()
            archive = Path(b["fx"]["original_root"])
            obj = archive / "objects/fixture"
            obj.chmod(0o700)
            path = obj / "ecb_usd_eur.raw"
            rows = list(csv.reader(io.StringIO(path.read_text())))
            if token == "wrong_currency":rows[1][2] = "INR"
            elif token == "wrong_unit":rows[1][8] = "INVERSE_QUOTE"
            else:rows[1][7] = token
            body = csv_bytes(rows[0], rows[1:])
            path.chmod(0o600); private_file(path, body, 0o400)
            source = {"bytes": len(body), "sha256": digest(body)}
            b["fx"]["source_originals"]["ecb_usd_eur"] = source
            manifest_path = obj / "manifest.json"
            m = json.loads(manifest_path.read_bytes())
            for r in m["originals"]:
                if r["source_id"] == "ecb_usd_eur":r.update(source)
            manifest_path.chmod(0o600); private_file(manifest_path, canon(m), 0o400)
            b["fx"]["source_receipt_sha256"] = digest(canon(m))
            obj.chmod(0o500)
            with self.subTest(token=token):
                with self.assertRaises(join.JoinError):join.load_join_inputs(b, p)

    def test_manifest_noncanonical_duplicate_and_panel_tamper_cannot_be_blessed_by_hash(self):
        for alteration in ("whitespace", "duplicate", "panel"):
            b, p = self.fixtures()
            obj = Path(b["commodity"]["panel_root"]) / "objects/panel"
            if alteration == "panel":
                path = obj / "panel.csv"
                body = path.read_bytes().replace(b",2000,", b",3000,", 1)
                private_file(path, body)
                b["commodity"]["panel_sha256"] = digest(body)
                b["commodity"]["panel_bytes"] = len(body)
            else:
                path = obj / "manifest.json"
                body = path.read_bytes()
                body = (b" " + body) if alteration == "whitespace" else (b'{"run_id":"panel",' + body[1:])
                private_file(path, body)
                b["commodity"]["panel_manifest_sha256"] = digest(body)
            with self.subTest(alteration=alteration):
                with self.assertRaises(join.JoinError):join.load_join_inputs(b, p)

    def test_separate_output_root_never_writes_component_archive(self):
        b, p, out = self.candidate()
        for path in (Path(b["commodity"]["panel_root"]), Path(b["fx"]["original_root"]) / "joined-child"):
            with self.subTest(path=path):
                with self.assertRaises(join.JoinError):join.publish_joined_panel(out, b, p, path, "separate")
                self.assertFalse((path / "objects/separate").exists())

    def test_postlink_read_cancel_and_final_verifier_close_faults_disqualify(self):
        for fault in ("read", "cancel", "verify_close"):
            b, p, out = self.candidate()
            root = self.root / ("postlink-" + fault)
            real_read, real_sync, real_close = os.read, os.fsync, os.close
            triggered, closes = [], []
            def read(fd, size):
                marker = root / "complete/run.json"
                if fault == "read" and marker.exists() and os.fstat(fd).st_ino == marker.stat().st_ino and not triggered:
                    triggered.append(True); raise OSError("final artifact read fault")
                return real_read(fd, size)
            def sync(fd):
                positive = root / "complete"
                if fault == "cancel" and positive.exists() and os.fstat(fd).st_ino == positive.stat().st_ino and not triggered:
                    triggered.append(True); raise KeyboardInterrupt()
                real_sync(fd)
            def close(fd):
                target = root.exists() and os.fstat(fd).st_ino == root.stat().st_ino
                real_close(fd)
                if target:
                    closes.append(True)
                    if fault == "verify_close" and len(closes) == 2:
                        triggered.append(True); raise OSError("final verifier close fault")
            with self.subTest(fault=fault), patch.object(join.os, "read", side_effect=read), \
                 patch.object(join.os, "fsync", side_effect=sync), patch.object(join.os, "close", side_effect=close):
                with self.assertRaisesRegex(join.JoinError, "PUBLICATION_INCOMPLETE"):
                    join.publish_joined_panel(out, b, p, root, "run")
            self.assertTrue(triggered)
            self.assertTrue((root / "complete/run.json").exists())
            self.assertEqual(json.loads((root / "incomplete/run.json").read_bytes())["reason"], "POSTLINK_FINALIZATION_FAILED")


if __name__ == "__main__":unittest.main()
