"""Action agent: RFQ drafts from recommendations, approve or reject, and the audit log.

No email is ever sent. Drafts are stored for a human to copy and send.
"""
from __future__ import annotations

import json
import os

import pandas as pd

from . import db

VALID_STATUS = {"draft", "approved", "rejected"}


class ActionError(ValueError):
    pass


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame rows as dicts with NULLs as None (pandas turns NULL into NaN)."""
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


def get_recommendation(rec_id: str) -> dict:
    df = db.query_df("SELECT * FROM recommendations WHERE id = ?", [rec_id])
    if df.empty:
        raise ActionError(f"Recommendation {rec_id} not found")
    row = df.iloc[0]
    return {"id": row["id"], "created_at": row["created_at"], "kind": row["kind"],
            "request": json.loads(row["request_json"]), "result": json.loads(row["result_json"])}


def build_rfq(rec: dict, company: str | None = None) -> dict:
    """Compose subject and body for an RFQ from a stored recommendation."""
    res, req = rec["result"], rec["request"]
    company = company or os.getenv("COMPANY_NAME", "").strip()
    if "schedule" not in res:
        raise ActionError("RFQ drafts can only be built from a purchase recommendation")
    name = res["display_name"]
    lines = []
    for r in res["schedule"]:
        delivery = r["month"] + int(req["supplier_lead_time_months"])
        lines.append(f"  - {r['kg']:,.0f} kg, order {r['month_label'].lower()}, delivery by month {delivery} "
                     f"(reference price band INR {r['p10_inr_per_kg']:,.2f} to {r['p50_inr_per_kg']:,.2f} per kg)")
    total = sum(r["kg"] for r in res["schedule"])
    subject = f"Request for quotation: {name}, {total:,.0f} kg over {len(res['schedule'])} delivery lot(s)"
    body = "\n".join([
        "Dear Supplier team,",
        "",
        f"We would like to request a quotation for {name} for delivery to our plant in Pune, India.",
        "",
        "Quantities and timing:",
        *lines,
        "",
        "Please quote in USD per metric tonne and INR per kg (CIF India, duty excluded), with payment terms, "
        "validity period and confirmed lead time for each lot.",
        "The reference price bands above are our internal estimates (10th to 50th percentile) and are shared "
        "only to indicate our expected range.",
        "",
        "Please reply within 5 working days.",
        "",
        "Regards,",
        "Procurement team" + (f", {company}" if company else ""),
    ])
    return {"subject": subject, "body": body, "material": res["material"], "total_kg": total,
            "lots": res["schedule"], "recommendation_summary": res["summary"]}


def create_rfq(rec_id: str) -> dict:
    rec = get_recommendation(rec_id)
    payload = build_rfq(rec)
    now = db.now_iso()
    action = {"id": db.new_id(), "recommendation_id": rec_id, "type": "rfq", "status": "draft",
              "payload_json": json.dumps(payload), "reason": None, "created_at": now, "updated_at": now}
    db.insert_row("actions", action)
    return get_action(action["id"])


def get_action(action_id: str) -> dict:
    df = db.query_df("SELECT * FROM actions WHERE id = ?", [action_id])
    if df.empty:
        raise ActionError(f"Action {action_id} not found")
    r = _records(df)[0]
    r["payload"] = json.loads(r.pop("payload_json"))
    return r


def set_status(action_id: str, status: str, reason: str | None = None) -> dict:
    if status not in VALID_STATUS:
        raise ActionError(f"Invalid status {status}")
    current = get_action(action_id)
    if current["status"] != "draft":
        raise ActionError(f"Action {action_id} is already {current['status']}")
    db.execute("UPDATE actions SET status = ?, reason = ?, updated_at = ? WHERE id = ?",
               [status, reason, db.now_iso(), action_id])
    return get_action(action_id)


def list_actions() -> list[dict]:
    df = db.query_df("SELECT * FROM actions ORDER BY created_at DESC")
    out = []
    for r in _records(df):
        r["payload"] = json.loads(r.pop("payload_json"))
        out.append(r)
    return out


def audit_log(limit: int = 200) -> list[dict]:
    """Every recommendation and every action, newest first, with input snapshots."""
    recs = db.query_df("SELECT id, created_at, kind, request_json, result_json FROM recommendations")
    acts = db.query_df("SELECT * FROM actions")
    events = []
    for r in _records(recs):
        res = json.loads(r["result_json"])
        events.append({"time": r["created_at"], "event": r["kind"], "id": r["id"], "recommendation_id": r["id"],
                       "status": None, "summary": res.get("summary") or res.get("diff"),
                       "input": json.loads(r["request_json"]), "reason": None})
    for a in _records(acts):
        p = json.loads(a["payload_json"])
        events.append({"time": a["created_at"], "event": f"{a['type']}_created", "id": a["id"],
                       "recommendation_id": a["recommendation_id"], "status": "draft", "summary": p.get("subject"),
                       "input": None, "reason": None})
        if a["status"] != "draft":
            events.append({"time": a["updated_at"], "event": f"{a['type']}_{a['status']}", "id": a["id"],
                           "recommendation_id": a["recommendation_id"], "status": a["status"],
                           "summary": p.get("subject"), "input": None, "reason": a["reason"]})
    events.sort(key=lambda e: (e["time"], e["event"]), reverse=True)
    return events[:limit]
