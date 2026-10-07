"""Shipment milestone ingestion: raw provider or scenario records become validated events."""
from .milestones import EVENT_TYPES, normalize_milestones

__all__ = ["EVENT_TYPES", "normalize_milestones"]
