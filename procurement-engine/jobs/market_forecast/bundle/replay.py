"""Hash-verified, offline reference replay of selected October 16 model scores.

This is a control-room adapter, not a ZOORQ integration or trading service.
Only run bundles and joblib artifacts received from a trusted source.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import sys
from datetime import date
from pathlib import Path

import joblib
import numpy as np

HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_path(relative: str, expected: str) -> Path:
    path = (HERE / relative).resolve()
    if not path.is_relative_to(HERE) or not path.is_file():
        raise ValueError(f"bundle path escapes or is absent: {relative}")
    if sha256(path) != expected:
        raise ValueError(f"bundle hash mismatch: {relative}")
    return path


def check_bundle(expected_manifest_sha256: str) -> dict:
    if sha256(HERE / "manifest.json") != expected_manifest_sha256:
        raise ValueError("manifest does not match trusted out-of-band SHA-256")
    manifest = json.loads((HERE / "manifest.json").read_text())
    for relative, expected in manifest["files_sha256"].items():
        checked_path(relative, expected)
    runtime = json.loads((HERE / "runtime_lock.json").read_text())
    if ".".join(map(str, sys.version_info[:3])) != runtime["python"]:
        raise ValueError("Python runtime differs from saved-model lock")
    for package, version in runtime["packages"].items():
        if importlib.metadata.version(package) != version:
            raise ValueError(f"package runtime differs: {package}")
    registry = json.loads((HERE / "registry_selected.json").read_text())
    if registry["anchor_date"] != "2026-10-16":
        raise ValueError("unexpected reference anchor")
    if len(registry["commodity_cells"]) != 9 or len(registry["fx_cells"]) != 9:
        raise ValueError("selected cell count differs")
    basis = registry["landed_basis"]
    checked_path(basis["constants_relative_path"], basis["constants_sha256"])
    checked_path(basis["formula_code_relative_path"], basis["formula_code_sha256"])
    return registry


def ref_path(reference: dict) -> Path:
    return checked_path(reference["relative_path"], reference["sha256"])


def close(actual: float, expected: float, name: str) -> None:
    if not math.isfinite(actual) or not math.isclose(
        actual, float(expected), rel_tol=1e-10, abs_tol=1e-10
    ):
        raise AssertionError(f"October 16 replay differs: {name}: {actual} != {expected}")


def main(expected_manifest_sha256: str) -> dict:
    registry = check_bundle(expected_manifest_sha256)
    sys.path.insert(0, str(HERE / "code"))
    from decision_evaluation import domestic_per_usd, landed_unit, load_constants
    from fx_core import feature_row
    from joint_features import load_daily, price_features

    anchor = date.fromisoformat(registry["anchor_date"])
    panel = {
        asset: load_daily(ref_path(reference), anchor)
        for asset, reference in registry["prices"].items()
    }
    if set(panel) != {"COPPER", "EURUSD", "GBPUSD", "USDJPY"}:
        raise ValueError("four canonical price files required")
    if any(rows[-1][0] != anchor for rows in panel.values()):
        raise ValueError("October 16 close absent")
    terms = load_constants()
    copper_by_day = dict(panel["COPPER"])
    joint_outputs = []
    for cell in registry["commodity_cells"]:
        country, expected = cell["country"], cell["expected"]
        common = set(copper_by_day) & set(dict(panel[country]))
        if max(common) != anchor:
            raise ValueError("joint exact-date anchor missing")
        copper_index = next(i for i, (day, _) in enumerate(panel["COPPER"]) if day == anchor)
        fx_index = next(i for i, (day, _) in enumerate(panel[country]) if day == anchor)
        copper_features = price_features([v for _, v in panel["COPPER"]], copper_index)
        fx_features = price_features([v for _, v in panel[country]], fx_index)
        features = {**{"cu_" + k: v for k, v in copper_features.items()},
                    **{"fx_" + k: v for k, v in fx_features.items()}}
        columns = cell["feature_order"]
        if columns != sorted(features):
            raise ValueError("joint feature order differs")
        x = np.asarray([[features[name] for name in columns]], dtype=float)
        if not np.isfinite(x).all():
            raise ValueError("nonfinite joint feature")
        values = {}
        for task, family in (("direction", "logistic"), ("magnitude", "ridge")):
            reference = cell["models"][task]
            metadata = json.loads(ref_path(reference["metadata"]).read_text())
            if (metadata["cell"] != cell["cell"] or metadata["family"] != family
                    or metadata["feature_columns"] != columns
                    or metadata["training_last_target"] != reference["training_last_target"]):
                raise ValueError("joint model metadata/feature contract differs")
            # Joblib is pickle-based. Hash and provenance checks precede every load.
            model = joblib.load(ref_path(reference["artifact"]))
            if model.n_features_in_ != len(columns):
                raise ValueError("joint model feature width differs")
            values[task] = float(model.predict_proba(x)[0, 1] if task == "direction"
                                 else model.predict(x)[0])
        predicted_return = values["magnitude"] * cell["magnitude_shrink"]
        issue_copper, issue_fx = copper_by_day[anchor], dict(panel[country])[anchor]
        issue_landed = landed_unit(issue_copper, domestic_per_usd(country, issue_fx), terms)
        close(values["direction"], expected["prob_landed_cost_up_uncalibrated"], cell["cell"] + "/direction")
        close(predicted_return, expected["predicted_landed_return"], cell["cell"] + "/magnitude")
        close(issue_landed, expected["issue_landed_unit_domestic_per_ton"], cell["cell"] + "/landed_unit")
        if (values["direction"] >= 0.5) != (expected["predicted_direction"] == "UP"):
            raise AssertionError("joint direction label differs")
        joint_outputs.append({"cell": cell["cell"], "anchor_date": anchor.isoformat(),
                              "score_landed_cost_up_uncalibrated": values["direction"],
                              "predicted_landed_return_shrunk": predicted_return,
                              "issue_landed_unit_domestic_per_ton": issue_landed,
                              "action_confidence_threshold": cell["direction_action_threshold"],
                              "target_date": None, "actual_return": None})
    fx_outputs = []
    for cell in registry["fx_cells"]:
        asset, expected = cell["asset"], cell["expected"]
        features = feature_row(asset, anchor, panel)
        columns = cell["feature_order"]
        if columns != sorted(columns) or not set(columns).issubset(features):
            raise ValueError("FX feature order differs")
        x = np.asarray([[features[name] for name in columns]], dtype=float)
        if not np.isfinite(x).all():
            raise ValueError("nonfinite FX feature")
        loaded = joblib.load(ref_path(cell["model"]))
        metadata, model = loaded["metadata"], loaded["model"]
        if (metadata["feature_order"] != columns or metadata["asset"] != asset
                or metadata["horizon"] != cell["horizon"]
                or metadata["config"] != cell["config"]
                or metadata["trained_label_end"] != cell["training_last_target"]):
            raise ValueError("FX model metadata/feature contract differs")
        if model.n_features_in_ != len(columns):
            raise ValueError("FX model feature width differs")
        if cell["config"]["family"] == "rbf_svc":
            margin = float(model.decision_function(x)[0])
            economic = 1 / (1 + math.exp(-max(-700, min(700, margin))))
        else:
            classes = list(model.classes_)
            economic = float(model.predict_proba(x)[0, classes.index(1)])
        raw = 1 - economic if asset in ("EURUSD", "GBPUSD") else economic
        close(economic, expected["prob_domestic_usd_cost_up_uncalibrated"], cell["key"] + "/economic")
        close(raw, expected["prob_raw_quote_up_uncalibrated"], cell["key"] + "/raw_quote")
        if (raw >= 0.5) != (expected["predicted_raw_quote_direction"] == "UP"):
            raise AssertionError("FX raw quote direction differs")
        fx_outputs.append({"cell": cell["key"], "anchor_date": anchor.isoformat(),
                           "score_domestic_usd_cost_up_uncalibrated": economic,
                           "score_raw_quote_up_uncalibrated": raw,
                           "action_confidence_threshold": cell["direction_action_threshold"],
                           "usage": "INFORMATIONAL_DISPLAY_ONLY_NO_PAYMENT_DEFERRAL_AUTHORITY",
                           "target_date": None, "actual_return": None})
    return {"schema": "portable-reference-replay.v1",
            "status": "ALL_27_SELECTED_MODELS_HASH_VERIFIED_AND_REPLAYED",
            "reference_only": True, "manifest_sha256": expected_manifest_sha256,
            "anchor_date": anchor.isoformat(), "joint": joint_outputs, "fx": fx_outputs}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = main(arguments.expected_manifest_sha256)
    content = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if arguments.output:
        arguments.output.write_text(content)
    else:
        print(content, end="")
