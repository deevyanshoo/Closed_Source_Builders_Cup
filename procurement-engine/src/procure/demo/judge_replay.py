"""Point-in-time projection of curated public advisories and separate simulation."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timezone


def _stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Replay evidence requires a timezone')
    return parsed.astimezone(timezone.utc)


def replay_at(replay: dict, as_of: datetime) -> dict:
    if as_of.tzinfo is None:
        raise ValueError('as_of must be timezone-aware')
    when = as_of.astimezone(timezone.utc)
    evidence = []
    for item in replay.get('advisories', []):
        if not isinstance(item, dict) or not item.get('published_at'):
            continue
        published = item['published_at']
        if item.get('date_precision') == 'day':
            try:
                cutoff = datetime.combine(date.fromisoformat(published), time.max, timezone.utc)
            except ValueError:
                continue
        else:
            cutoff = _stamp(published)
        if cutoff <= when:
            evidence.append(deepcopy(item))
    evidence.sort(key=lambda row: (row['published_at'], row.get('evidence_id', '')))
    simulation = replay.get('simulation')
    if isinstance(simulation, dict) and simulation.get('alert_at'):
        simulation = deepcopy(simulation) if _stamp(simulation['alert_at']) <= when else None
    else:
        simulation = None
    return {'schema': 1, 'replay_id': replay.get('replay_id'),
            'as_of': as_of.isoformat(), 'documented_evidence': evidence,
            'simulation': simulation,
            'warning': 'Historical carrier advisories do not establish an affected company shipment or a three-week early warning.'}
