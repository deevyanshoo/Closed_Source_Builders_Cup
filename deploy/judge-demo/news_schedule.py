"""Validate billing evidence and print prepare-only Cloud news steps.

This module never invokes gcloud or changes a Google Cloud resource.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys


PROJECT = 'closed-source-builder-261007'
REGION = 'us-central1'
WORKER = 'closed-source-news-worker'
SCHEDULER = 'closed-source-news-scheduler'
JOB = 'closed-source-news-refresh'
SCHEDULE = 'closed-source-news-hourly'
APP_IDENTITY = f'closed-source-demo@{PROJECT}.iam.gserviceaccount.com'
MAX_BUNDLE_BYTES = 2 * 1024 * 1024


class Refused(ValueError):
    """Unsafe or unverified activation request."""


def _instant(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise Refused(f'{field} must be an ISO UTC time')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise Refused(f'{field} must be an ISO UTC time') from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise Refused(f'{field} must be an ISO UTC time')
    return parsed.astimezone(timezone.utc)


def _positive(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise Refused(f'{field} must be positive')
    return float(value)


def validate_gate(proof: dict, now: datetime) -> str:
    """Accept only fresh, project-scoped trial or measured strict-free evidence."""
    if not isinstance(proof, dict) or now.tzinfo is None:
        raise Refused('A proof object and timezone-aware current time are required')
    if proof.get('project_id') != PROJECT:
        raise Refused('Proof project does not match the demo project')
    observed = _instant(proof.get('observed_at'), 'observed_at')
    now = now.astimezone(timezone.utc)
    if not timedelta(0) <= now - observed <= timedelta(minutes=15):
        raise Refused('Billing observation is stale or in the future')
    mode = proof.get('mode')
    if mode == 'trial_credits':
        if proof.get('source') != 'authenticated_google_cloud_billing_overview' or proof.get('account_type') != 'free_trial':
            raise Refused('Authenticated Free Trial account evidence is required')
        if proof.get('paid_upgrade') is not False:
            raise Refused('Paid upgrade must be explicitly false')
        _positive(proof.get('credit_remaining'), 'credit_remaining')
        _positive(proof.get('days_remaining'), 'days_remaining')
        if _instant(proof.get('expires_at'), 'expires_at') <= now:
            raise Refused('Free Trial has expired')
        return mode
    if mode == 'strict_free':
        if proof.get('source') != 'billing_account_metered_usage':
            raise Refused('Strict free mode needs metered billing-account evidence')
        if not re.fullmatch(r'billingAccounts/[A-Z0-9-]+', str(proof.get('billing_account', ''))):
            raise Refused('Billing-account scope is required')
        usage = proof.get('account_wide_usage')
        if not isinstance(usage, dict):
            raise Refused('Account-wide usage is required')
        limits = {'scheduler_jobs_after_create': 3, 'cloud_run_vcpu_seconds_after_create': 240000,
                  'cloud_run_gib_seconds_after_create': 450000, 'storage_gb_month_after_create': 5,
                  'storage_class_a_after_create': 5000, 'storage_class_b_after_create': 50000,
                  'storage_egress_gb_after_create': 100}
        for field, limit in limits.items():
            value = usage.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value > limit:
                raise Refused(f'Unknown or exhausted strict-free allowance: {field}')
        return mode
    raise Refused('Unknown cost mode')


def _step(name: str, argv: list[str], *, creates_resource=False, check_argv=None) -> dict:
    step = {'name': name, 'argv': argv, 'creates_resource': creates_resource}
    if creates_resource:
        step['check_argv'] = check_argv
        step['on_exists'] = 'verify_equivalent_or_stop'
    return step


def prepare(
    proof: dict, *, bucket: str, image: str, now: datetime | None = None,
    region: str = REGION, timeout_seconds: int = 120, tasks: int = 1,
    parallelism: int = 1, cpu: int = 1, memory_mib: int = 512,
    job_retries: int = 0, scheduler_retries: int = 0,
    retention_days: int = 7, bundle_max_bytes: int = MAX_BUNDLE_BYTES,
) -> dict:
    """Return reviewable command argv and companion file content, never apply it."""
    now = now or datetime.now(timezone.utc)
    mode = validate_gate(proof, now)
    bounds = (region, timeout_seconds, tasks, parallelism, cpu, memory_mib,
              job_retries, scheduler_retries, retention_days, bundle_max_bytes)
    if bounds != (REGION, 120, 1, 1, 1, 512, 0, 0, 7, MAX_BUNDLE_BYTES):
        raise Refused('Worker, schedule, storage or retention bounds differ from the approved configuration')
    if not isinstance(bucket, str) or not (bucket == f'{PROJECT}-news-snapshots'
            or re.fullmatch(r'closed-source-news-[a-z0-9-]{8,43}', bucket)):
        raise Refused('A dedicated, DNS-safe news bucket name is required')
    if not isinstance(image, str) or not re.fullmatch(
        rf'[a-z0-9-]+-docker\.pkg\.dev/{PROJECT}/[a-z0-9-]+/[a-z0-9-]+@sha256:[a-f0-9]{{64}}', image
    ):
        raise Refused('An immutable image digest from the demo project is required')

    worker_email = f'{WORKER}@{PROJECT}.iam.gserviceaccount.com'
    scheduler_email = f'{SCHEDULER}@{PROJECT}.iam.gserviceaccount.com'
    bucket_uri = f'gs://{bucket}'
    job_uri = f'https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/jobs/{JOB}:run'
    commands = [
        _step('enable_scheduler_api', ['gcloud', 'services', 'enable', 'cloudscheduler.googleapis.com', f'--project={PROJECT}']),
        _step('create_private_bucket', ['gcloud', 'storage', 'buckets', 'create', bucket_uri,
             f'--project={PROJECT}', f'--location={REGION}', '--default-storage-class=STANDARD',
             '--uniform-bucket-level-access', '--public-access-prevention', '--soft-delete-duration=0s'],
             creates_resource=True, check_argv=['gcloud', 'storage', 'buckets', 'describe', bucket_uri, '--format=json']),
        _step('set_seven_day_lifecycle', ['gcloud', 'storage', 'buckets', 'update', bucket_uri,
             '--lifecycle-file=lifecycle.json']),
        _step('create_worker_identity', ['gcloud', 'iam', 'service-accounts', 'create', WORKER,
             f'--project={PROJECT}', '--display-name=Hourly public news worker'], creates_resource=True,
             check_argv=['gcloud', 'iam', 'service-accounts', 'describe', worker_email, f'--project={PROJECT}']),
        _step('create_scheduler_identity', ['gcloud', 'iam', 'service-accounts', 'create', SCHEDULER,
             f'--project={PROJECT}', '--display-name=Hourly news scheduler'], creates_resource=True,
             check_argv=['gcloud', 'iam', 'service-accounts', 'describe', scheduler_email, f'--project={PROJECT}']),
    ]
    for name, member, role in (
        ('worker_object_creator', worker_email, 'roles/storage.objectCreator'),
        ('worker_object_viewer', worker_email, 'roles/storage.objectViewer'),
        ('app_object_viewer', APP_IDENTITY, 'roles/storage.objectViewer'),
    ):
        commands.append(_step(name, ['gcloud', 'storage', 'buckets', 'add-iam-policy-binding', bucket_uri,
                        f'--member=serviceAccount:{member}', f'--role={role}']))
    commands.extend([
        _step('create_bounded_worker_job', ['gcloud', 'run', 'jobs', 'create', JOB,
             f'--project={PROJECT}', f'--region={REGION}', f'--image={image}',
             f'--service-account={worker_email}', '--tasks=1', '--parallelism=1',
             '--max-retries=0', '--task-timeout=120s', '--cpu=1', '--memory=512Mi',
             '--command=python', '--args=-m,procure.demo.news_refresh',
             f'--set-env-vars=NEWS_SNAPSHOT_BUCKET={bucket}'], creates_resource=True,
             check_argv=['gcloud', 'run', 'jobs', 'describe', JOB, f'--project={PROJECT}', f'--region={REGION}', '--format=json']),
        _step('grant_job_invoker_to_scheduler', ['gcloud', 'run', 'jobs', 'add-iam-policy-binding', JOB,
             f'--project={PROJECT}', f'--region={REGION}',
             f'--member=serviceAccount:{scheduler_email}', '--role=roles/run.invoker']),
        _step('create_hourly_oauth_schedule', ['gcloud', 'scheduler', 'jobs', 'create', 'http', SCHEDULE,
             f'--project={PROJECT}', f'--location={REGION}', '--schedule=0 * * * *',
             '--time-zone=UTC', f'--uri={job_uri}', '--http-method=POST', '--message-body={}',
             '--headers=Content-Type=application/json',
             f'--oauth-service-account-email={scheduler_email}',
             '--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform',
             '--attempt-deadline=30s', '--max-retry-attempts=0'], creates_resource=True,
             check_argv=['gcloud', 'scheduler', 'jobs', 'describe', SCHEDULE,
                         f'--project={PROJECT}', f'--location={REGION}', '--format=json']),
    ])
    fingerprint = hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {
        'mode': 'prepare_only', 'activation_gate': mode, 'project': PROJECT,
        'region': REGION, 'proof_sha256': fingerprint,
        'prepared_at': now.astimezone(timezone.utc).isoformat(),
        'monthly_projection': {'max_scheduled_runs_31_days': 744,
                               'max_vcpu_seconds': 89280, 'max_gib_seconds': 44640,
                               'max_new_bundle_bytes': 744 * MAX_BUNDLE_BYTES},
        'files': {'lifecycle.json': {'rule': [{'action': {'type': 'Delete'}, 'condition': {'age': 7}}]}},
        'commands': commands,
        'release_note': 'Inspect current resource state and trial status before execution. This plan is not activation evidence and never upgrades billing.',
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args(argv)
    try:
        proof = json.loads(args.evidence.read_text(encoding='utf-8'))
        plan = prepare(proof, bucket=args.bucket, image=args.image)
    except (OSError, json.JSONDecodeError, Refused) as exc:
        print(f'Refused: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
