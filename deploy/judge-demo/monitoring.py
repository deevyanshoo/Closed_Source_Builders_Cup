"""Prepare Cloud Monitoring JSON for the existing demo service and collector.

This module prints reviewable API payloads only. It has no cloud client or apply path.
"""
from __future__ import annotations

import argparse
import json


PROJECT = 'closed-source-builder-261007'
REGION = 'asia-south1'
SERVICE = 'closed-source-quote-demo'
JOB = 'closed-source-news-refresh'


def _service_filter(metric: str, *, response_class: str | None = None,
                    exclude_websocket: bool = False) -> str:
    result = (f'metric.type="run.googleapis.com/{metric}" AND '
              f'resource.type="cloud_run_revision" AND '
              f'resource.labels.service_name="{SERVICE}" AND '
              f'resource.labels.location="{REGION}"')
    if response_class:
        result += f' AND metric.labels.response_code_class="{response_class}"'
    if exclude_websocket:
        result += ' AND metric.labels.response_code!="101"'
    return result


def _job_filter() -> str:
    return (f'metric.type="run.googleapis.com/job/completed_execution_count" AND '
            f'resource.type="cloud_run_job" AND '
            f'resource.labels.job_name="{JOB}" AND '
            f'resource.labels.location="{REGION}" AND '
            'metric.labels.result="failed"')


def _aggregation(aligner: str, period: str = '300s') -> dict:
    return {'alignmentPeriod': period, 'perSeriesAligner': aligner,
            'crossSeriesReducer': 'REDUCE_SUM'} if aligner == 'ALIGN_SUM' else {
                'alignmentPeriod': period, 'perSeriesAligner': aligner}


def _chart(title: str, metric_filter: str, aligner: str, y: int, period: str = '300s') -> dict:
    return {'xPos': 0, 'yPos': y, 'width': 12, 'height': 6,
            'widget': {'title': title, 'xyChart': {'dataSets': [{
                'timeSeriesQuery': {'timeSeriesFilter': {
                    'filter': metric_filter, 'aggregation': _aggregation(aligner, period)}},
                'plotType': 'LINE'}]}}}


def _policy(title: str, metric_filter: str, aligner: str,
            threshold: float, *, period: str = '300s', duration: str = '0s') -> dict:
    return {'displayName': title, 'combiner': 'OR', 'enabled': True,
            'notificationChannels': [], 'conditions': [{
                'displayName': title,
                'conditionThreshold': {'filter': metric_filter,
                    'aggregations': [_aggregation(aligner, period)],
                    'comparison': 'COMPARISON_GT', 'thresholdValue': threshold,
                    'duration': duration}}]}


def prepare() -> dict:
    """Return dashboard/alert-policy create payloads; no API calls or commands."""
    error_filter = _service_filter('request_count', response_class='5xx')
    latency_filter = _service_filter('request_latencies', exclude_websocket=True)
    job_filter = _job_filter()
    return {'mode': 'prepare_only', 'project': PROJECT,
            'dashboard': {'displayName': 'Closed Source demo operations',
                'mosaicLayout': {'columns': 12, 'tiles': [
                    _chart('Service 5xx requests per five minutes', error_filter, 'ALIGN_SUM', 0),
                    _chart('Service p95 request latency (ms)', latency_filter,
                           'ALIGN_PERCENTILE_95', 6, '60s'),
                    _chart('Failed news collector executions', job_filter, 'ALIGN_SUM', 12)]}},
            'alert_policies': [
                _policy('Demo service 5xx errors', error_filter, 'ALIGN_SUM', 5),
                _policy('Demo service p95 latency', latency_filter,
                        'ALIGN_PERCENTILE_95', 60_000, period='60s', duration='300s'),
                _policy('Demo news collector failure', job_filter, 'ALIGN_SUM', 0)]}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(json.dumps(prepare(), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
