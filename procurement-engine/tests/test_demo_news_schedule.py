"""Release gate and prepared command contract for the hourly news worker."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'deploy/judge-demo/news_schedule.py'
spec = importlib.util.spec_from_file_location('news_schedule', SCRIPT)
news_schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(news_schedule)
NOW = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)
BUCKET = 'closed-source-news-963802031195'
IMAGE = 'asia-south1-docker.pkg.dev/closed-source-builder-261007/closed-source-demo/quote-to-plan@sha256:' + 'a' * 64


def trial_proof():
    return {
        'mode': 'trial_credits', 'observed_at': '2026-10-14T23:55:00Z',
        'source': 'authenticated_google_cloud_billing_overview',
        'project_id': 'closed-source-builder-261007', 'account_type': 'free_trial',
        'paid_upgrade': False, 'credit_remaining': 28405.26,
        'days_remaining': 65, 'expires_at': '2026-12-19T00:00:00Z',
    }


@pytest.mark.parametrize('change', [
    {'account_type': 'paid'}, {'paid_upgrade': True},
    {'credit_remaining': 0}, {'credit_remaining': None},
    {'days_remaining': 0}, {'expires_at': '2026-10-14T23:59:59Z'},
    {'observed_at': '2026-10-13T00:00:00Z'},
    {'observed_at': '2026-10-15T00:05:00Z'},
    {'source': 'unverified'}, {'project_id': 'another-project'},
])
def test_trial_gate_rejects_paid_expired_stale_or_unverified_proof(change):
    proof = {**trial_proof(), **change}
    with pytest.raises(news_schedule.Refused):
        news_schedule.prepare(proof, bucket=BUCKET, image=IMAGE, now=NOW)


def test_trial_gate_does_not_accept_a_bare_free_flag():
    with pytest.raises(news_schedule.Refused):
        news_schedule.prepare({'mode': 'trial_credits', 'free': True}, bucket=BUCKET, image=IMAGE, now=NOW)


@pytest.mark.parametrize('change', [
    {'region': 'asia-south1'}, {'timeout_seconds': 121}, {'tasks': 2},
    {'parallelism': 2}, {'cpu': 2}, {'memory_mib': 1024},
    {'job_retries': 1}, {'scheduler_retries': 1}, {'retention_days': 8},
    {'bundle_max_bytes': 2 * 1024 * 1024 + 1},
])
def test_unsafe_worker_or_storage_bound_is_refused(change):
    with pytest.raises(news_schedule.Refused):
        news_schedule.prepare(trial_proof(), bucket=BUCKET, image=IMAGE, now=NOW, **change)


def test_strict_free_requires_account_wide_measured_headroom():
    proof = {**trial_proof(), 'mode': 'strict_free'}
    with pytest.raises(news_schedule.Refused):
        news_schedule.prepare(proof, bucket=BUCKET, image=IMAGE, now=NOW)


def strict_free_proof():
    return {
        **trial_proof(), 'mode': 'strict_free', 'source': 'billing_account_metered_usage',
        'billing_account': 'billingAccounts/TEST-ONLY',
        'account_wide_usage': {
            'scheduler_jobs_after_create': 1,
            'cloud_run_vcpu_seconds_after_create': 89280,
            'cloud_run_gib_seconds_after_create': 44640,
            'storage_gb_month_after_create': 1,
            'storage_class_a_after_create': 1000,
            'storage_class_b_after_create': 1000,
            'storage_egress_gb_after_create': 20,
        },
    }


def test_trial_can_prepare_mumbai_job_while_bucket_and_scheduler_stay_in_us():
    prepared = news_schedule.prepare(trial_proof(), bucket=BUCKET, image=IMAGE,
                                     now=NOW, job_region='asia-south1')
    assert prepared['region'] == 'us-central1'
    assert prepared['job_region'] == 'asia-south1'
    commands = {step['name']: step for step in prepared['commands']}
    assert '--location=us-central1' in commands['create_private_bucket']['argv']
    assert '--region=asia-south1' in commands['create_bounded_worker_job']['argv']
    assert '--region=asia-south1' in commands['create_bounded_worker_job']['check_argv']
    assert '--region=asia-south1' in commands['grant_job_invoker_to_scheduler']['argv']
    schedule = commands['create_hourly_oauth_schedule']['argv']
    assert '--location=us-central1' in schedule
    assert '--uri=https://run.googleapis.com/v2/projects/closed-source-builder-261007/locations/asia-south1/jobs/closed-source-news-refresh:run' in schedule


def test_strict_free_proof_cannot_prepare_mumbai_job():
    with pytest.raises(news_schedule.Refused):
        news_schedule.prepare(strict_free_proof(), bucket=BUCKET, image=IMAGE,
                              now=NOW, job_region='asia-south1')


@pytest.mark.parametrize('job_region', ['europe-west1', 'asia-south2', 'us-east1', ''])
def test_unapproved_job_regions_are_refused(job_region):
    with pytest.raises(news_schedule.Refused):
        news_schedule.prepare(trial_proof(), bucket=BUCKET, image=IMAGE,
                              now=NOW, job_region=job_region)


def test_valid_trial_prepares_private_bounded_resources_without_running_commands():
    prepared = news_schedule.prepare(trial_proof(), bucket=BUCKET, image=IMAGE, now=NOW)
    assert prepared['mode'] == 'prepare_only'
    assert prepared['activation_gate'] == 'trial_credits'
    assert prepared['job_region'] == 'us-central1'
    assert prepared['monthly_projection']['max_vcpu_seconds'] == 89280
    assert prepared['monthly_projection']['max_gib_seconds'] == 44640
    commands = prepared['commands']
    assert all(command['argv'][0] == 'gcloud' for command in commands)
    assert all(command['on_exists'] == 'verify_equivalent_or_stop' for command in commands if command['creates_resource'])
    argv = [command['argv'] for command in commands]
    bucket_command = next(a for a in argv if a[:4] == ['gcloud', 'storage', 'buckets', 'create'])
    assert '--location=us-central1' in bucket_command
    assert '--default-storage-class=STANDARD' in bucket_command
    assert '--uniform-bucket-level-access' in bucket_command
    assert '--public-access-prevention' in bucket_command
    assert '--soft-delete-duration=0s' in bucket_command
    worker = next(a for a in argv if a[:4] == ['gcloud', 'run', 'jobs', 'create'])
    assert '--tasks=1' in worker and '--parallelism=1' in worker
    assert '--max-retries=0' in worker and '--task-timeout=120s' in worker
    assert '--cpu=1' in worker and '--memory=512Mi' in worker
    assert '--command=python' in worker and '--args=-m,procure.demo.news_refresh' in worker
    scheduler = next(a for a in argv if a[:4] == ['gcloud', 'scheduler', 'jobs', 'create'])
    assert '--schedule=0 * * * *' in scheduler and '--time-zone=UTC' in scheduler
    assert '--max-retry-attempts=0' in scheduler
    assert '--oauth-service-account-email=closed-source-news-scheduler@closed-source-builder-261007.iam.gserviceaccount.com' in scheduler
    assert '--http-method=POST' in scheduler
    assert '--uri=https://run.googleapis.com/v2/projects/closed-source-builder-261007/locations/us-central1/jobs/closed-source-news-refresh:run' in scheduler
    lifecycle = prepared['files']['lifecycle.json']
    assert lifecycle['rule'][0]['condition'] == {'age': 7}
    assert lifecycle['rule'][0]['action'] == {'type': 'Delete'}
    roles = [flag for command in argv for flag in command if flag.startswith('--role=')]
    assert '--role=roles/storage.objectCreator' in roles
    assert roles.count('--role=roles/storage.objectViewer') == 2
    assert '--role=roles/run.invoker' in roles
    assert not any('--role=roles/storage.admin' in a or '--allow-unauthenticated' in a for a in argv)
