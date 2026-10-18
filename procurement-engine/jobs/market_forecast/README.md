# Isolated selected-model reference job

Build remotely from repository root after source freeze:

```sh
python3 procurement-engine/jobs/market_forecast/prepare_job_package.py /private/tmp/zoorq-market-job-context
gcloud builds submit /private/tmp/zoorq-market-job-context --project=closed-source-builder-261007 --region=asia-south1 --config=/private/tmp/zoorq-market-job-context/cloudbuild.yaml
```

The image uses exact Python 3.11.15 and five pinned numerical packages. Run its default `--verify-only` command on Linux first. It checks the trusted manifest SHA, all 59 bundle file hashes and all 27 saved scores within 1e-10 before any publication. Only after review, set `ZOORQ_PREDICTION_BUCKET=closed-source-builder-261007-predictions` and `ZOORQ_JOB_IMAGE_DIGEST=sha256:<built-image-digest>` on the private Cloud Run Job, then override command to `python /app/runner.py --publish`. The source clocks remain from October 16–17; publication mode is `REFERENCE_REPLAY`. Keep Scheduler paused.

Set `ZOORQ_PREDICTION_JOB_IMAGE_DIGEST=sha256:<the same exact built-image-digest>` on the app only after verifying the archive pointer. The app reader requires this exact digest and the pinned source-code, package, runtime, cell and receipt identities; otherwise it shows a labeled bundled reference fallback. Add only this app environment name to a strict release allowlist.

Archive writes are create-only. The sole replaceable key is `control/latest.json`, advanced with `ifGenerationMatch`; a conflict requires rereading and review. No company data enters the bucket.
