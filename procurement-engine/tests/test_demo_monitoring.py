"""Prepared Monitoring resources must be scoped to the existing demo service/job."""
import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / 'deploy/judge-demo/monitoring.py'
spec = importlib.util.spec_from_file_location('demo_monitoring', SCRIPT)
monitoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitoring)


def test_prepare_emits_only_dashboard_and_three_unrouted_policies():
    prepared = monitoring.prepare()
    assert prepared['mode'] == 'prepare_only'
    assert set(prepared) == {'mode', 'project', 'dashboard', 'alert_policies'}
    assert prepared['project'] == 'closed-source-builder-261007'
    assert len(prepared['alert_policies']) == 3
    assert len(prepared['dashboard']['mosaicLayout']['tiles']) == 3
    assert all(policy['notificationChannels'] == [] for policy in prepared['alert_policies'])
    assert all(policy['combiner'] == 'OR' and policy['enabled'] for policy in prepared['alert_policies'])
    serialized = json.dumps(prepared)
    assert 'notificationChannels/ ' not in serialized
    assert not any(word in serialized.lower() for word in ('email_address', 'iam', 'serviceusage', 'create_job'))


def test_native_metrics_are_exactly_scoped_to_existing_service_and_job():
    prepared = monitoring.prepare()
    policies = {policy['displayName']: policy for policy in prepared['alert_policies']}
    assert set(policies) == {'Demo service 5xx errors', 'Demo service p95 latency',
                             'Demo news collector failure'}
    service = 'resource.labels.service_name="closed-source-quote-demo"'
    job = 'resource.labels.job_name="closed-source-news-refresh"'
    thresholds = [p['conditions'][0]['conditionThreshold'] for p in policies.values()]
    assert all(t['duration'] in ('0s', '300s') and t['comparison'] == 'COMPARISON_GT'
               for t in thresholds)
    assert all(service in t['filter'] for t in thresholds[:2])
    assert job in thresholds[2]['filter']
    assert 'metric.labels.result="failed"' in thresholds[2]['filter']
    assert 'run.googleapis.com/request_count' in thresholds[0]['filter']
    assert 'metric.labels.response_code_class="5xx"' in thresholds[0]['filter']
    assert 'run.googleapis.com/request_latencies' in thresholds[1]['filter']
    assert 'metric.labels.response_code!="101"' in thresholds[1]['filter']
    assert thresholds[1]['thresholdValue'] == 60_000
    assert thresholds[1]['aggregations'][0]['perSeriesAligner'] == 'ALIGN_PERCENTILE_95'
    assert 'run.googleapis.com/job/completed_execution_count' in thresholds[2]['filter']


def test_prepare_is_deterministic_json_without_cloud_side_effects(capsys):
    assert monitoring.prepare() == monitoring.prepare()
    monitoring.main([])
    assert json.loads(capsys.readouterr().out) == monitoring.prepare()
