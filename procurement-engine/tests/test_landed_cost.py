from __future__ import annotations

import numpy as np
import pytest

from procure.landed_cost import LandedAssumptions, get_landed_assumptions, landed_inr_per_kg

A = LandedAssumptions(duty_pct=0.05, freight_inr_per_kg=6.0, holding_cost_pct_per_month=0.01)


def test_landed_cost_hand_checked():
    # 800 * 1.05 + 6 + 800 * 0.01 * 2 = 840 + 6 + 16 = 862
    assert landed_inr_per_kg(800.0, 2, A) == pytest.approx(862.0)


def test_no_holding_when_bought_just_in_time():
    assert landed_inr_per_kg(800.0, 0, A) == pytest.approx(846.0)
    assert landed_inr_per_kg(800.0, -1, A) == pytest.approx(846.0)


def test_vectorised():
    out = landed_inr_per_kg(np.array([100.0, 200.0]), np.array([0, 1]), A)
    np.testing.assert_allclose(out, [100 * 1.05 + 6, 200 * 1.05 + 6 + 2])


def test_assumptions_loaded_from_config():
    a = get_landed_assumptions("copper")
    assert 0 <= a.duty_pct < 1 and a.freight_inr_per_kg >= 0 and a.holding_cost_pct_per_month > 0
