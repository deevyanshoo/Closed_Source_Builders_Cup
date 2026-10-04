"""Streamlit UI for the AI Procurement Engine. Talks to the FastAPI backend only."""
from __future__ import annotations

import os

import httpx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

API = os.getenv("PROCURE_API_URL", "http://localhost:8000").rstrip("/")

# Reference categorical palette (light steps), fixed order: blue, orange, aqua.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
GRID = "rgba(128,128,128,0.18)"

st.set_page_config(page_title="Procurement Engine", page_icon="📦", layout="wide")


# ----------------------------------------------------------------------------- API helpers

def api(method: str, path: str, **kw):
    try:
        r = httpx.request(method, f"{API}{path}", timeout=300, **kw)
    except httpx.HTTPError as exc:
        st.error(f"Could not reach the engine at {API}. Is the API running? ({exc})")
        st.stop()
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        st.error(f"The engine returned an error ({r.status_code}): {detail}")
        return None
    return r.json()


@st.cache_data(ttl=60, show_spinner=False)
def cached_get(path: str):
    return api("GET", path)


def clear_cache():
    cached_get.clear()


def inr(x: float, decimals: int = 0) -> str:
    """Indian-style rupee formatting with lakh and crore for large values."""
    if abs(x) >= 1e7:
        return f"INR {x / 1e7:,.2f} cr"
    if abs(x) >= 1e5:
        return f"INR {x / 1e5:,.2f} lakh"
    return f"INR {x:,.{decimals}f}"


BADGE = {"live": "🟢 Live", "cache": "🟡 Cached", "synthetic": "🔴 Synthetic", "sample": "🟠 Sample", "none": "⚪ None"}
ACTION_COLOR = {"BUY NOW": "#c0392b", "SPLIT BUY": "#b9770e", "WAIT": "#1e8449"}


def base_layout(fig: go.Figure, height: int = 380, y_title: str = "") -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                      yaxis=dict(title=y_title, gridcolor=GRID, zeroline=False),
                      xaxis=dict(gridcolor=GRID, showgrid=False))
    return fig


def fan_chart(fc: dict, schedule: list[dict] | None = None, title: str = "") -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=fc["history_months"], y=fc["history_inr_per_kg"], name="Actual price",
                             line=dict(color=BLUE, width=2), hovertemplate="INR %{y:,.2f}/kg"))
    x = fc["months"]
    fig.add_trace(go.Scatter(x=x + x[::-1], y=fc["p90"] + fc["p10"][::-1], fill="toself", name="Likely range (P10 to P90)",
                             fillcolor="rgba(42,120,214,0.15)", line=dict(width=0), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=fc["p50"], name="Middle forecast (P50)", line=dict(color=BLUE, width=2, dash="dot"),
                             hovertemplate="INR %{y:,.2f}/kg"))
    if schedule:
        bx = [x[r["month"]] for r in schedule]
        by = [fc["p50"][r["month"]] for r in schedule]
        fig.add_trace(go.Scatter(x=bx, y=by, mode="markers+text", name="Planned buys",
                                 marker=dict(color=ORANGE, size=12, line=dict(color="white", width=2)),
                                 text=[f"{r['percent']:.0f}%" for r in schedule], textposition="top center",
                                 hovertemplate="Buy %{text}<extra></extra>"))
    fig.update_layout(title=title)
    return base_layout(fig, y_title="INR per kg")


def recommendation_view(rec: dict, key: str, show_rfq: bool = True) -> None:
    color = ACTION_COLOR.get(rec["action"], "#333")
    st.markdown(f"<div style='font-size:2.2rem;font-weight:700;color:{color}'>{rec['action']}</div>"
                f"<div style='font-size:1.2rem'>{rec['summary']}</div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Price today", f"INR {rec['current_inr_per_kg']:,.2f}/kg", help=f"Latest monthly average: {rec['latest_data_month']}")
    c2.metric("Price weather", f"{rec['price_weather']['probability'] * 100:.0f}%", help=rec["price_weather"]["text"])
    conf = rec["confidence"]
    c3.metric("Confidence", conf["level"], help=" ".join(conf["reasons"]))
    st.caption(f"🌦️ {rec['price_weather']['text']}.  Confidence {conf['level']}: {' '.join(conf['reasons'])}")
    for w in rec["warnings"]:
        st.warning(w)

    st.markdown("**Buying schedule**")
    sched = pd.DataFrame(rec["schedule"])[["month_label", "calendar_month", "percent", "kg", "expected_inr_per_kg",
                                           "expected_landed_inr_per_kg", "p10_inr_per_kg", "p90_inr_per_kg"]]
    sched.columns = ["When", "Month", "Share %", "Quantity kg", "Expected price INR/kg", "Expected landed INR/kg",
                     "Low case INR/kg (P10)", "High case INR/kg (P90)"]
    st.dataframe(sched, hide_index=True, width="stretch")

    st.markdown("**How this plan compares**")
    comp = pd.DataFrame(rec["comparison"])
    comp = comp.assign(**{"Expected cost": comp["expected_cost_inr"].map(inr), "High case cost (P90)": comp["p90_cost_inr"].map(inr),
                          "Saving vs just in time": comp["saving_vs_jit_inr"].map(inr),
                          "Saving %": comp["saving_vs_jit_pct"].map(lambda v: f"{v:+.2f}%")})
    st.dataframe(comp[["name", "schedule", "Expected cost", "High case cost (P90)", "Saving vs just in time", "Saving %"]]
                 .rename(columns={"name": "Plan", "schedule": "Schedule"}), hide_index=True, width="stretch")

    st.plotly_chart(fan_chart(rec["forecast"], rec["schedule"], f"{rec['display_name']} price in INR per kg"),
                    width="stretch", key=f"fan_{key}")
    st.caption(f"The last month of real data ({rec['latest_data_month']}) is treated as today's price. "
               f"Forecast model: {rec['model']['selected_model']}.")

    st.markdown("**Why this recommendation**")
    why = pd.DataFrame([{"Signal": w["signal"], "Value": w["value"], "Detail": w["detail"]} for w in rec["why"]])
    st.dataframe(why, hide_index=True, width="stretch")
    news_item = next((w for w in rec["why"] if w.get("headlines")), None)
    if news_item:
        with st.expander("Headlines that moved the news index most"):
            for h in news_item["headlines"]:
                tag = " (sample)" if h["is_sample"] else ""
                st.write(f"{'⬆️' if h['direction'] == 'up' else '⬇️'} {h['title']}{tag}  "
                         f"(severity {h['severity']:.2f}, {h['age_days']:.0f} days old, contribution {h['contribution']:+.2f})")

    if show_rfq and rec.get("id"):
        if st.button("Draft RFQ for suppliers", key=f"rfq_{key}", type="primary"):
            a = api("POST", "/actions/rfq", json={"recommendation_id": rec["id"]})
            if a:
                st.success("RFQ draft saved. Review it in the Actions and audit tab. No email has been sent.")
                st.text_input("Subject", a["payload"]["subject"], key=f"subj_{key}")
                st.code(a["payload"]["body"], language=None)


# ----------------------------------------------------------------------------- page

st.title("📦 Procurement Engine")
st.caption("When to buy imported copper, aluminium and iron ore, how much, and why. Prototype.")

health = cached_get("/health")
if health is None:
    st.stop()
mats = cached_get("/materials") or []
MAT_KEYS = [m["key"] for m in mats]
MAT_NAMES = {m["key"]: m["display_name"] for m in mats}

if health["any_synthetic"] or health["any_sample"]:
    parts = []
    if health["any_synthetic"]:
        parts.append("some price or exchange-rate data is **synthetic** (generated, not real)")
    if health["any_sample"]:
        parts.append("some news is **bundled sample headlines**, not real news")
    st.warning("⚠️ Demo data in use: " + " and ".join(parts) + ". See the Data status tab.")
if health["offline"]:
    st.info("Offline mode is on: only cached or fallback data is used.")

tabs = st.tabs(["Overview", "Plan a purchase", "Headline to impact", "Regret meter", "Actions and audit", "Data status"])

# ----------------------------------------------------------------------------- 1 overview
with tabs[0]:
    cards = cached_get("/overview") or []
    cols = st.columns(len(cards) or 1)
    for col, c in zip(cols, cards):
        with col:
            with st.container(border=True):
                st.subheader(c["display_name"])
                st.metric("Price per kg", f"INR {c['inr_per_kg']:,.2f}", f"{c['change_1m_pct']:+.2f}% in 1 month",
                          delta_color="inverse")
                st.write(f"3-month change: **{c['change_3m_pct']:+.2f}%**")
                st.write(f"🌦️ {c['price_weather']['text']}")
                ni = c["news_index"]
                mood = "supply risk, prices may rise" if ni > 0.2 else "prices may ease" if ni < -0.2 else "calm"
                st.write(f"News risk: **{ni:+.2f}** ({mood})")
                st.progress((ni + 1) / 2, text="-1 ease  ·  0 calm  ·  +1 shock")
                st.caption(f"Prices: {BADGE.get(c['price_source'], c['price_source'])}  ·  "
                           f"News: {BADGE.get(c['news_source'], c['news_source'])}  ·  "
                           f"Data to {c['latest_month']}  ·  USD {c['usd_per_unit']:,.0f}/t at {c['usdinr']:.2f} INR/USD")
                if c["model_used"] == "naive":
                    st.caption("No model beat a simple random walk, so no price direction is assumed.")

# ----------------------------------------------------------------------------- 2 plan
with tabs[1]:
    with st.form("plan"):
        c1, c2, c3, c4 = st.columns(4)
        material = c1.selectbox("Material", MAT_KEYS, format_func=lambda k: MAT_NAMES[k], key="plan_mat")
        qty = c2.number_input("Quantity needed (kg)", 1000, 10_000_000, 50_000, step=5000)
        need_by = c3.slider("Needed in (months)", 1, 6, 3)
        lead = c4.slider("Supplier lead time (months)", 0, 3, 1)
        c5, c6, c7, c8 = st.columns(4)
        stock = c5.number_input("Stock on hand (kg)", 0, 10_000_000, 20_000, step=5000)
        cons = c6.number_input("Monthly use (kg)", 1, 10_000_000, 15_000, step=1000)
        risk = c7.select_slider("Risk appetite", ["low", "medium", "high"], value="medium",
                                help="Low: prefers steady, hedged buying. High: accepts more price risk to save on average.")
        budget = c8.number_input("Max budget INR (0 = none)", 0, 10_000_000_000, 0, step=1_000_000)
        go_btn = st.form_submit_button("Get recommendation", type="primary")
    if go_btn:
        body = {"material": material, "quantity_kg": qty, "need_by_month": need_by, "current_stock_kg": stock,
                "monthly_consumption_kg": cons, "supplier_lead_time_months": lead, "risk_appetite": risk}
        if budget:
            body["max_budget_inr"] = budget
        with st.spinner("Simulating 2,000 price paths..."):
            st.session_state["plan_rec"] = api("POST", "/recommend", json=body)
    if st.session_state.get("plan_rec"):
        recommendation_view(st.session_state["plan_rec"], "plan")

# ----------------------------------------------------------------------------- 3 headline
EXAMPLES = [
    "Major copper mine strike halts 10% of global supply",
    "Major aluminium smelter outage halts 10% of global supply",
    "Aluminium surplus grows as new smelters ramp up",
    "Iron ore export ban announced by major supplier",
    "Iron ore inventory build at Chinese ports as steel demand slump deepens",
]
with tabs[2]:
    st.write("Paste a news headline to see how it would change the buying plan.")
    ex_cols = st.columns(len(EXAMPLES))
    for i, (col, ex) in enumerate(zip(ex_cols, EXAMPLES)):
        if col.button(ex, key=f"ex_{i}"):
            st.session_state["headline_text"] = ex
    headline = st.text_area("Headline", key="headline_text", height=70)
    c1, c2, c3, c4 = st.columns(4)
    h_mat = c1.selectbox("Material", ["auto"] + MAT_KEYS, format_func=lambda k: "Detect from headline" if k == "auto" else MAT_NAMES[k])
    h_stock = c2.number_input("Stock on hand (kg)", 0, 10_000_000, 20_000, step=5000, key="h_stock")
    h_need = c3.slider("Needed in (months)", 1, 6, 3, key="h_need")
    h_risk = c4.select_slider("Risk appetite", ["low", "medium", "high"], value="medium", key="h_risk")
    if st.button("Show impact", type="primary", disabled=not headline.strip()):
        req = {"material": h_mat if h_mat != "auto" else "copper", "quantity_kg": 50000, "need_by_month": h_need,
               "current_stock_kg": h_stock, "monthly_consumption_kg": 15000, "supplier_lead_time_months": 1,
               "risk_appetite": h_risk}
        with st.spinner("Re-running the plan with this headline..."):
            st.session_state["impact"] = api("POST", "/headline-impact", json={
                "material": None if h_mat == "auto" else h_mat, "headline": headline, "request": req})
    out = st.session_state.get("impact")
    if out:
        s = out["signal"]
        st.markdown("**What the engine read from the headline**")
        st.dataframe(pd.DataFrame([{"Material": MAT_NAMES.get(s["material"], s["material"]), "Relevant": s["relevant"],
                                    "Price direction": s["direction"], "Severity (0 to 1)": s["severity"],
                                    "Horizon (months)": s["horizon_months"], "Reason": s["reason"],
                                    "Read by": "Gemini" if s["extractor"] == "llm" else "Keyword rules"}]),
                     hide_index=True, width="stretch")
        st.info(out["diff"])
        st.caption(out["note"])
        b, a = st.columns(2)
        with b:
            st.markdown("### Before")
            recommendation_view(out["before"], "before", show_rfq=False)
        with a:
            st.markdown("### After")
            recommendation_view(out["after"], "after", show_rfq=False)

# ----------------------------------------------------------------------------- 4 regret meter
with tabs[3]:
    st.write("How would this engine have done over the last 36 months, using only the data available at the time? "
             "Each month it plans a standard purchase, and the plan is priced at the prices that actually happened.")
    c1, c2 = st.columns(2)
    bt_mat = c1.selectbox("Material", MAT_KEYS, format_func=lambda k: MAT_NAMES[k], key="bt_mat")
    measure = c2.selectbox("Risk measure", ["regret", "overshoot"],
                           format_func=lambda m: "Regret vs best month (default)" if m == "regret" else "Cost overshoot (P90 minus average)")
    bt = cached_get(f"/backtest/{bt_mat}?risk_measure={measure}")
    if bt:
        sm = bt["summary"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total saved vs just in time", inr(sm["total_saved_vs_jit_inr"]))
        m2.metric("Average saving", f"{sm['avg_saving_vs_jit_pct']:+.2f}%")
        m3.metric("Months beating just in time", f"{sm['share_months_beat_jit'] * 100:.0f}%")
        m4.metric(f"Worst month ({sm['worst_month']['origin']})", inr(sm["worst_month"]["saving_inr"]),
                  help=sm["worst_month"]["decision"])
        m5, m6, m7, m8 = st.columns(4)
        m5.metric(f"Best month ({sm['best_month']['origin']})", inr(sm["best_month"]["saving_inr"]),
                  help=sm["best_month"]["decision"])
        m6.metric("Saved vs buy all now", inr(sm["vs_buy_now"]["total_inr"]))
        m7.metric("Saved vs even split", inr(sm["vs_even_split"]["total_inr"]))
        m8.metric("Average share bought immediately", f"{sm['avg_buy_now_pct']:.0f}%")
        if sm["total_saved_vs_jit_inr"] < 0:
            st.warning("Over this period the engine cost more than simply buying just in time. This is reported as is.")
        rows = pd.DataFrame(bt["rows"])
        fig = go.Figure()
        for col, name, color in (("saving_vs_jit", "Engine", BLUE),
                                 (None, "Buy all now", ORANGE), (None, "Even split", AQUA)):
            if col:
                y = rows[col].cumsum()
            elif name == "Buy all now":
                y = (rows["jit_cost"] - rows["buy_now_cost"]).cumsum()
            else:
                y = (rows["jit_cost"] - rows["even_split_cost"]).cumsum()
            fig.add_trace(go.Scatter(x=rows["origin"], y=y, name=name, line=dict(color=color, width=2),
                                     hovertemplate="INR %{y:,.0f}"))
        fig.add_hline(y=0, line=dict(color="gray", width=1))
        st.plotly_chart(base_layout(fig, y_title="Cumulative INR saved vs just in time"), width="stretch")
        rg = pd.DataFrame(sm["regret_vs_hindsight"]).T.rename(columns={"avg_pct": "Average extra cost vs hindsight %",
                                                                       "worst_pct": "Worst month %"})
        st.markdown("**Extra cost compared with perfect hindsight** (lower is better)")
        st.dataframe(rg.round(2), width="stretch")
        st.markdown("**Month by month**")
        st.dataframe(rows[["origin", "decision", "price_change_pct", "policy_cost", "jit_cost", "buy_now_cost",
                           "even_split_cost", "saving_vs_jit", "saving_vs_jit_pct"]].rename(columns={
            "origin": "Month", "decision": "Engine plan", "price_change_pct": "Price change over window %",
            "policy_cost": "Engine cost INR", "jit_cost": "Just in time INR", "buy_now_cost": "Buy all now INR",
            "even_split_cost": "Even split INR", "saving_vs_jit": "Saved vs JIT INR", "saving_vs_jit_pct": "Saved %"}),
            hide_index=True, width="stretch")
        for n in bt["notes"]:
            st.caption(n)

# ----------------------------------------------------------------------------- 5 actions
with tabs[4]:
    acts = api("GET", "/actions") or []
    st.subheader("RFQ drafts")
    if not acts:
        st.write("No RFQ drafts yet. Create one from the Plan a purchase tab.")
    for a in acts:
        icon = {"draft": "📝", "approved": "✅", "rejected": "❌"}[a["status"]]
        with st.expander(f"{icon} {a['payload']['subject']}  ({a['status']}, {a['created_at'][:16].replace('T', ' ')})",
                         expanded=a["status"] == "draft"):
            st.code(a["payload"]["body"], language=None)
            if a["status"] == "draft":
                reason = st.text_input("Reason (optional)", key=f"reason_{a['id']}")
                c1, c2 = st.columns(2)
                if c1.button("Approve", key=f"ap_{a['id']}", type="primary"):
                    api("POST", f"/actions/{a['id']}/approve", json={"reason": reason or None})
                    st.rerun()
                if c2.button("Reject", key=f"rj_{a['id']}"):
                    api("POST", f"/actions/{a['id']}/reject", json={"reason": reason or None})
                    st.rerun()
            elif a.get("reason"):
                st.caption(f"Reason: {a['reason']}")
    st.subheader("Audit log")
    audit = api("GET", "/audit") or []
    if audit:
        df = pd.DataFrame(audit)
        df["input"] = df["input"].map(lambda v: "" if v is None else str(v)[:200])
        st.dataframe(df[["time", "event", "summary", "status", "reason", "id", "recommendation_id", "input"]],
                     hide_index=True, width="stretch")

# ----------------------------------------------------------------------------- 6 data status
with tabs[5]:
    c1, c2, c3 = st.columns(3)
    c1.metric("Latest price month", (health["latest_price_month"] or "")[:7])
    c2.metric("Latest exchange-rate month", (health["latest_fx_month"] or "")[:7])
    c3.metric("News reader", "Gemini" if health["gemini_enabled"] else "Keyword rules")
    src = pd.DataFrame([{"Source": k, "Used": BADGE.get(v["used"], v["used"]), "Fetched": v["fetched_at"],
                         "Rows": v["rows"], "Status": v["status"], "Message": v["message"]}
                        for k, v in health["sources"].items()])
    st.dataframe(src, hide_index=True, width="stretch")
    st.caption(f"Database: {health['db_engine']}. Gradient boosting backend: {health['gbm_backend']}.")
    if st.button("Refresh data", type="primary"):
        with st.spinner("Fetching prices, exchange rates and news, then re-testing models. This can take a minute..."):
            rep = api("POST", "/ingest/refresh")
        clear_cache()
        if rep:
            st.success(f"Refreshed in {rep['timings_s'].get('total', 0):.0f} s.")
            st.rerun()
    st.subheader("Forecast accuracy (walk-forward, last 36 months)")
    met = pd.DataFrame(cached_get("/metrics") or [])
    if not met.empty:
        for mat in MAT_KEYS:
            sub = met[met["material"] == mat]
            sel = sub[sub["selected"]]["model"].iloc[0] if sub["selected"].any() else "?"
            st.markdown(f"**{MAT_NAMES[mat]}** (model in use: {sel})")
            piv = sub.pivot_table(index="horizon", columns="model", values=["mape", "coverage"])
            piv.columns = [f"{'Error (MAPE)' if a == 'mape' else 'Band coverage'} {b}" for a, b in piv.columns]
            st.dataframe((piv * 100).round(1).astype(str) + "%", width="stretch")
        st.caption("Error is the average percentage miss of the middle forecast. Band coverage is the share of real "
                   "outcomes inside the P10 to P90 range (target about 80%). A model is used only if it beats the "
                   "naive random walk by at least 2%.")
