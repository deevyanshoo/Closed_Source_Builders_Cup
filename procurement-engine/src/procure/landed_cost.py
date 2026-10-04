"""Landed cost per kg in INR: price plus duty, freight and holding cost.

All rates come from config/assumptions.yaml and are demo assumptions, not official rates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .settings import assumptions


@dataclass(frozen=True)
class LandedAssumptions:
    duty_pct: float
    freight_inr_per_kg: float
    holding_cost_pct_per_month: float

    def as_dict(self) -> dict:
        return asdict(self)


def get_landed_assumptions(material: str) -> LandedAssumptions:
    cfg = assumptions()["landed_cost"]
    m = cfg["materials"].get(material, {})
    return LandedAssumptions(duty_pct=float(m.get("duty_pct", 0.0)),
                             freight_inr_per_kg=float(m.get("freight_inr_per_kg", 0.0)),
                             holding_cost_pct_per_month=float(cfg["holding_cost_pct_per_month"]))


def landed_inr_per_kg(price_inr_per_kg, months_held, a: LandedAssumptions):
    """price * (1 + duty) + freight + holding, where holding = price * pct * months held early.

    Works element-wise on numpy arrays. months_held below zero is treated as zero.
    """
    price = np.asarray(price_inr_per_kg, dtype=float)
    held = np.maximum(np.asarray(months_held, dtype=float), 0.0)
    holding = price * a.holding_cost_pct_per_month * held
    return price * (1.0 + a.duty_pct) + a.freight_inr_per_kg + holding
