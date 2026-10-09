"""News snapshots for the point-in-time event gate (#49). Offline by default; see fetch.py and snapshots.py."""
from .fetch import fetch_gdelt_items, fetch_rss_items, parse_gdelt, parse_rss, recorded_fetches
from .snapshots import CANDIDATE_NOTE, build_snapshots, gate_rows, load_sources, tag_entities

__all__ = ["CANDIDATE_NOTE", "build_snapshots", "fetch_gdelt_items", "fetch_rss_items", "gate_rows", "load_sources",
           "parse_gdelt", "parse_rss", "recorded_fetches", "tag_entities"]
