"""Forecasting: models, walk-forward selection and Monte Carlo simulation."""
from __future__ import annotations


def clear_caches() -> None:
    from .models import clear_cache

    clear_cache()
