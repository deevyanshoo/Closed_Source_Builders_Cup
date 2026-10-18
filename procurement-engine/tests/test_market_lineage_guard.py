"""Reject a self-consistent archive that rewrites trusted replay lineage."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

from procure.demo.market_snapshot_store import canonical, digest, verify_envelope
from procure.demo.market_snapshot_store import TRUSTED_PACKAGE_SOURCE_SHA256


REFERENCE = json.loads((Path(__file__).parents[1] / "config" /
                        "market_reference_snapshot_v1.json").read_text())
IMAGE = "sha256:" + "a" * 64


def envelope(change=lambda snapshot: None):
    snapshot = deepcopy(REFERENCE)
    snapshot["run_id"] = "reference-test"
    snapshot["job_image_digest"] = IMAGE
    snapshot["source_code_sha256"] = TRUSTED_PACKAGE_SOURCE_SHA256
    snapshot["publication_mode"] = "REFERENCE_REPLAY"
    for cell in snapshot["forecasts"]:
        cell["run_id"] = snapshot["run_id"]
    change(snapshot)
    snapshot_raw = canonical(snapshot)
    receipt = {"contract_version": "prediction.v1", "run_id": snapshot["run_id"],
               "as_of_utc": snapshot["as_of_utc"], "snapshot_key": "runs/reference-test/snapshot.json",
               "snapshot_generation": "7", "snapshot_sha256": digest(snapshot_raw),
               **{key: snapshot[key] for key in ("model_manifest_sha256", "source_manifest_sha256",
                                                   "feature_spec_sha256", "job_image_digest",
                                                   "source_code_sha256")}}
    receipt_raw = canonical(receipt)
    pointer = {"contract_version": "prediction.v1", "run_id": snapshot["run_id"],
               "as_of_utc": snapshot["as_of_utc"],
               "job_image_digest": snapshot["job_image_digest"],
               "source_code_sha256": snapshot["source_code_sha256"],
               "snapshot": {"key": receipt["snapshot_key"], "generation": "7", "sha256": digest(snapshot_raw)},
               "receipt": {"key": "runs/reference-test/receipt.json", "generation": "8",
                           "sha256": digest(receipt_raw)}}
    return canonical(pointer), receipt_raw, snapshot_raw


class LineageTests(unittest.TestCase):
    def test_valid_exact_reference(self):
        self.assertEqual(verify_envelope(*envelope(), expected_image_digest=IMAGE)["run_id"],
                         "reference-test")

    def test_tampered_lineage_rejected_even_when_envelope_hashes_match(self):
        changes = [
            lambda s: s.update(as_of_utc="2099-01-01T00:00:00Z"),
            lambda s: s.update(job_image_digest="sha256:test"),
            lambda s: s["forecasts"][1].update(forecast_id=s["forecasts"][0]["forecast_id"]),
            lambda s: s["forecasts"][0].update(current_outcomes_observed=True),
            lambda s: s["forecasts"][9]["magnitude_context"].update(model_artifact_sha256="0" * 64),
            lambda s: s.update(runtime={**s["runtime"], "python": "3.12.0"}),
            lambda s: s.update(source_code_sha256="0" * 64),
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_envelope(*envelope(change))

    def test_worker_source_binding_matches_reader_pin(self):
        runner = Path(__file__).parents[1] / 'jobs' / 'market_forecast' / 'runner.py'
        spec = importlib.util.spec_from_file_location('market_forecast_runner', runner)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.package_source_sha256(), TRUSTED_PACKAGE_SOURCE_SHA256)


if __name__ == "__main__":
    unittest.main()
