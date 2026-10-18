# Portable selected-model reference replay

This is a control-room research adapter, not a ZOORQ integration. It copies the 27 selected October 16 artifacts, exact feature code, frozen landed-cost constants, and four public canonical daily price CSVs. No news, credentials, or customer data are included.

Run only a bundle received from a trusted source. Confirm the manifest SHA-256 against the coordinator handoff, then use the exact Python 3.11 package versions in `runtime_lock.json`. From any directory:

```sh
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 PYTHONPATH= python /path/to/portable_models/replay.py --expected-manifest-sha256 TRUSTED_MANIFEST_SHA256 --output /tmp/portable-replay.json
```

The replay verifies every bundled file hash before loading any pickle-based joblib object and compares all nine joint direction scores, nine shrunk landed-return estimates, and nine FX economic/raw-quote scores with the saved October 16 references to tolerance 1e-10. It produces no future target or realized outcome. Exchange-local closes are historical planning proxies, not executable prices.
