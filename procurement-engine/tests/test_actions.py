from __future__ import annotations

import pytest

from procure import actions
from procure.decision import PurchaseRequest, recommend


def test_rfq_lifecycle_and_audit(seeded_db):
    rec = recommend(PurchaseRequest(material="aluminium"))
    a = actions.create_rfq(rec["id"])
    assert a["status"] == "draft"
    body = a["payload"]["body"]
    assert body.startswith("Dear Supplier team,")
    assert "[" not in body and "Lorem" not in body
    assert a["payload"]["total_kg"] == pytest.approx(50000)

    ok = actions.set_status(a["id"], "approved", "price band acceptable")
    assert ok["status"] == "approved"
    with pytest.raises(actions.ActionError):
        actions.set_status(a["id"], "rejected")

    b = actions.create_rfq(rec["id"])
    rej = actions.set_status(b["id"], "rejected", "supplier list outdated")
    assert rej["reason"] == "supplier list outdated"

    events = actions.audit_log()
    kinds = {e["event"] for e in events}
    assert {"recommend", "rfq_created", "rfq_approved", "rfq_rejected"} <= kinds
    rec_event = next(e for e in events if e["id"] == rec["id"])
    assert rec_event["input"]["material"] == "aluminium"


def test_rfq_unknown_recommendation(seeded_db):
    with pytest.raises(actions.ActionError):
        actions.create_rfq("doesnotexist")
