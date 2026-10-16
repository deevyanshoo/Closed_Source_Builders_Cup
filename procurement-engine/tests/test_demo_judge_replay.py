import json
from datetime import datetime, timezone
from pathlib import Path

from procure.demo.judge_replay import replay_at


CONFIG = Path(__file__).parents[1] / 'config' / 'judge_suez_2021_v1.json'


def at(day):
    return datetime(2021, 3, day, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_living_advisory_is_gated_by_embedded_update_date():
    replay = json.loads(CONFIG.read_text())
    assert replay_at(replay, at(23))['documented_evidence'] == []
    march24 = replay_at(replay, at(24))['documented_evidence']
    assert march24 and all(row['published_at'][:10] <= '2021-03-24' for row in march24)
    assert not any('refloat' in row['fact'].lower() for row in march24)
    march29 = replay_at(replay, at(29))['documented_evidence']
    assert any('refloat' in row['fact'].lower() for row in march29)
    assert not any('transit resumed' in row['fact'].lower() for row in march29)
    assert any('transit resumed' in row['fact'].lower() for row in replay_at(replay, at(30))['documented_evidence'])


def test_synthetic_alert_and_user_delay_are_separate_from_evidence():
    replay = json.loads(CONFIG.read_text())
    replay['simulation'] = {'alert_at': '2021-03-24T12:00:00Z', 'assumed_delay_days': 14}
    result = replay_at(replay, at(26))
    assert result['simulation']['assumed_delay_days'] == 14
    assert all('14-day' not in row['fact'] for row in result['documented_evidence'])


def test_unknown_update_time_never_leaks_at_start_of_day():
    replay = json.loads(CONFIG.read_text())
    morning = datetime(2021, 3, 29, 12, tzinfo=timezone.utc)
    assert not any('refloat' in row['fact'].lower()
                   for row in replay_at(replay, morning)['documented_evidence'])
    assert all(row.get('date_precision') == 'day' for row in replay['advisories'])
