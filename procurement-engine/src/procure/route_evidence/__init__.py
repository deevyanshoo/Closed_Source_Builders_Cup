"""Route and hazard evidence for facilities and lanes (synthetic records only for now)."""
from .evidence import evidence_for_locations, normalize_route_evidence

__all__ = ["evidence_for_locations", "normalize_route_evidence"]
