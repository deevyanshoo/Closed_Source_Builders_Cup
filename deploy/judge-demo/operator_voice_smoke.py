#!/usr/bin/env python3
"""Black-box hosted operator and Google speech acceptance; network calls require --live.

One fresh session uses four operator turns (twelve reserved model calls) and two
speech calls. --with-procurement adds two turns (six calls) and rules-mode
synthetic quote preparation. It never sends an RFQ or stores raw audio in JSON.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import wave

from smoke import AcceptanceError, Client, base_url, denied_status, key, require


MODEL = "gemini-3.5-flash-lite"
ROLES = ("coordinator", "evidence", "procurement")
OFFICIAL = {"eia", "ecb", "ecb_fx"}


def valid_time(value: object, stage: str) -> datetime:
    require(isinstance(value, str), stage, "missing_timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AcceptanceError(stage, "invalid_timestamp") from None
    require(result.tzinfo is not None, stage, "invalid_timestamp")
    return result.astimezone(timezone.utc)


def bound(response: dict, company: str, context: int, stage: str) -> None:
    require(response.get("company_id") == company and response.get("context_version") == context,
            stage, "context_mismatch")


def news_check(news: dict, company: str, context: int, live: bool) -> dict:
    stage = "news"
    bound(news, company, context, stage)
    state = news.get("cache_state")
    checked = valid_time(news.get("checked_at"), stage)
    age = (datetime.now(timezone.utc) - checked).total_seconds()
    sources = news.get("sources")
    items, fx = news.get("items"), news.get("fx")
    require(isinstance(sources, list) and isinstance(items, list) and isinstance(fx, list),
            stage, "invalid_news_response")
    require(state in ("fresh", "cached", "stale", "empty", "unavailable", "offline"),
            stage, "invalid_cache_state")
    current = state in ("fresh", "cached") and -60 <= age <= 960
    healthy = [s for s in sources if isinstance(s, dict) and s.get("source_id") in OFFICIAL
               and s.get("state") == state and s.get("last_checked_at") == news.get("checked_at")]
    healthy_ids = {s["source_id"] for s in healthy}
    observations = [x for x in items if isinstance(x, dict) and
                    ((x.get("provider") == "EIA" and "eia" in healthy_ids) or
                     (x.get("provider") == "ECB" and "ecb" in healthy_ids)) and
                    x.get("synthetic") is False and x.get("retrieved_at") == news.get("checked_at")]
    rates = [x for x in fx if isinstance(x, dict) and x.get("provider") == "ECB" and
             "ecb_fx" in healthy_ids and x.get("retrieved_at") == news.get("checked_at")]
    if live:
        require(current and healthy and (observations or rates), stage, "no_current_official_observation")
    return {"cache_state": state, "checked_at": news.get("checked_at"),
            "age_seconds": round(age), "new_provider_fetch_observed": state == "fresh",
            "official_sources": sorted(s["source_id"] for s in healthy),
            "observed_items": len(observations), "fx_observations": len(rates),
            "sample_source_url": observations[0].get("url") if observations else None}


def matched_news(news: dict, company: dict) -> tuple[dict, str]:
    materials = {m.get("material_id") for m in company.get("materials", [])
                 if isinstance(m, dict)}
    for item in news.get("items", []):
        if not isinstance(item, dict) or not isinstance(item.get("item_id"), str) or not item["item_id"]:
            continue
        matched = item.get("matched_materials")
        if isinstance(matched, list):
            material = next((mid for mid in matched if mid in materials), None)
            if material is not None:
                return item, material
    raise AcceptanceError("explain", "no_explainable_news_item")


def intended_inspection(transcript: str) -> bool:
    """Reject a plausible but unrelated recognition of the owned voice fixture."""
    return (bool(re.search(r"\binspect\b", transcript, re.IGNORECASE)) and
            bool(re.search(r"\binventory\b", transcript, re.IGNORECASE)) and
            bool(re.search(r"\b(?:explain|stock)\b", transcript, re.IGNORECASE)))


def turn_check(turn: dict, company: str, context: int, expected_status: str, stage: str) -> dict:
    bound(turn, company, context, stage)
    require(turn.get("status") == expected_status and turn.get("read_only") is True and
            isinstance(turn.get("turn_id"), str) and bool(turn["turn_id"]) and
            isinstance(turn.get("assistant_text"), str) and bool(turn["assistant_text"].strip()),
            stage, "invalid_operator_turn")
    steps = turn.get("agent_steps")
    require(isinstance(steps, list) and len(steps) == 4, stage, "missing_agent_steps")
    for role, step in zip((*ROLES, "verifier"), steps):
        require(isinstance(step, dict) and step.get("agent") == role and
                step.get("status") == "completed" and isinstance(step.get("tool"), str) and
                bool(step["tool"]), stage, "invalid_agent_step")
        if role == "verifier":
            require(step.get("model") == "deterministic" and step.get("provider_call") == "none",
                    stage, "verifier_not_deterministic")
        else:
            require(step.get("model") == MODEL and step.get("provider_call") == "real",
                    stage, "model_receipt_not_real")
    require(isinstance(turn.get("evidence_ids"), list), stage, "invalid_evidence_ids")
    return {"turn_id": turn["turn_id"], "status": expected_status,
            "model": MODEL, "provider_calls": 3,
            "tools": {x["agent"]: x["tool"] for x in steps},
            "evidence_ids": turn["evidence_ids"],
            "assistant_text": turn["assistant_text"]}


def operator(client: Client, csrf: str, company: str, context: int, message: str,
             expected_status: str, stage: str, receipt: dict) -> dict:
    turn = client.json(stage, "/api/demo/operator", body={"message": message,
                       "expected_context_version": context, "idempotency_key": key()}, csrf=csrf)
    receipt["turns"].append(turn_check(turn, company, context, expected_status, stage))
    return turn


def proposal_check(proposal: object, kind: str, context: int, stage: str) -> dict:
    require(isinstance(proposal, dict) and proposal.get("kind") == kind and
            isinstance(proposal.get("proposal_id"), str) and bool(proposal["proposal_id"]) and
            isinstance(proposal.get("summary"), str) and bool(proposal["summary"]) and
            isinstance(proposal.get("parameters"), dict) and
            isinstance(proposal.get("offer_versions"), dict) and
            proposal.get("context_version") == context, stage, "invalid_proposal")
    require(valid_time(proposal.get("expires_at"), stage) > datetime.now(timezone.utc),
            stage, "expired_proposal")
    return proposal


def apply_once(client: Client, csrf: str, proposal: dict, company: str, context: int,
               kind: str, stage: str, receipt: dict) -> dict:
    body = {"proposal_id": proposal["proposal_id"], "reviewed": True,
            "expected_context_version": context, "idempotency_key": key()}
    denied_status(client, stage + "_review_required", "/api/demo/operator/confirm",
                  {**body, "reviewed": False, "idempotency_key": key()}, csrf, 422)
    first = client.json(stage, "/api/demo/operator/confirm", body=body, csrf=csrf)
    bound(first, company, context, stage)
    require(first.get("status") == "applied" and first.get("kind") == kind and
            isinstance(first.get("result"), dict) and isinstance(first.get("session"), dict),
            stage, "invalid_apply_result")
    replay = client.json(stage + "_replay", "/api/demo/operator/confirm", body=body, csrf=csrf)
    require(replay.get("result") == first["result"] and replay.get("kind") == kind,
            stage, "replay_created_second_effect")
    bound(replay, company, context, stage)
    receipt["effects"].append({"kind": kind, "result_id": first["result"].get(
        {"watch": "watch_id", "recovery": "comparison_id", "rfq": "draft_id"}[kind]),
        "replay_same_result": True})
    return first


def reviewed_plan(client: Client, csrf: str, context: int) -> tuple[dict, dict]:
    offers = {}
    for fixture in ("express-copper", "eu-copper"):
        extraction = client.json("rules_" + fixture, "/api/demo/extract",
                                 body={"fixture_id": fixture, "mode": "rules",
                                       "expected_context_version": context,
                                       "idempotency_key": key()}, csrf=csrf)
        offer = extraction.get("offer")
        require(isinstance(offer, dict) and offer.get("extractor") == "rules",
                "plan_setup", "rules_quote_missing")
        saved = client.json("save_" + fixture, "/api/demo/offers/" + offer["offer_id"] + "/save",
                            body={"expected_version": offer["version"],
                                  "expected_context_version": context,
                                  "idempotency_key": key()}, csrf=csrf)
        offers[fixture] = saved["offer"]
    request = {"offer_ids": [o["offer_id"] for o in offers.values()],
               "expected_versions": {o["offer_id"]: o["version"] for o in offers.values()},
               "material": "copper", "quantity_kg": "10000", "current_stock_kg": "2000",
               "need_by": "2026-11-05", "decision_date": "2026-10-14",
               "reporting_currency": "USD", "budget": "100000",
               "additional_cost_per_kg": "0.25", "expected_context_version": context}
    plan = client.json("reviewed_plan", "/api/demo/plan", body=request, csrf=csrf)
    require(isinstance(plan, dict) and plan.get("selected_offer_id") == offers["eu-copper"]["offer_id"]
            and plan.get("offer_versions") == request["expected_versions"],
            "plan_setup", "invalid_reviewed_plan")
    return offers, plan


def wav_input(path: Path) -> tuple[bytes, int]:
    try:
        data = path.read_bytes()
        with wave.open(str(path), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            valid = (audio.getnchannels() == 1 and audio.getsampwidth() == 2 and
                     audio.getcomptype() == "NONE" and 8000 <= rate <= 48000 and
                     0 < frames <= rate * 20)
    except (OSError, ValueError, wave.Error):
        raise AcceptanceError("audio_input", "invalid_wav") from None
    require(valid and data.startswith(b"RIFF") and len(data) <= 500_000,
            "audio_input", "invalid_wav")
    return data, rate


def write_owned(path: Path, data: bytes) -> None:
    target = path.expanduser().resolve()
    require(target.parent.is_dir() and not target.is_symlink() and not target.exists(),
            "output", "output_must_be_new")
    temporary = target.with_name(target.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise AcceptanceError("output", "write_failed") from None


def run(base: str, wav: Path, receipt: dict, *, live: bool, procurement: bool,
        audio_output: Path | None, client_factory=Client) -> None:
    client = client_factory(base)
    health = client.json("health", "/-/health")
    require(health.get("ready") is True and isinstance(health.get("revision"), str),
            "health", "invalid_health")
    receipt["revision"] = health["revision"]
    session = client.json("session", "/api/demo/session")
    csrf = session.get("csrf_token")
    company = session.get("company", {}).get("company_id")
    context = session.get("context_version")
    require(isinstance(csrf, str) and bool(csrf) and company == "solara-cables" and
            type(context) is int and session.get("offers") == [] and
            session.get("synthetic") is True, "session", "invalid_fresh_session")
    selected = client.json("select_atlas", "/api/demo/company",
                           body={"company_id": "atlas-petrochem",
                                 "expected_context_version": context,
                                 "idempotency_key": key()}, csrf=csrf)
    company = selected.get("company", {}).get("company_id")
    context = selected.get("context_version")
    require(company == "atlas-petrochem" and type(context) is int and
            context > session["context_version"] and selected.get("offers") == [],
            "select_atlas", "wrong_company_context")
    receipt["operator_company"] = company
    receipt["operator_context_version"] = context
    news = client.json("news", "/api/demo/news")
    receipt["news"] = news_check(news, company, context, live)
    relevant, material = matched_news(news, selected["company"])
    receipt["news"]["explained_item_id"] = relevant["item_id"]
    receipt["news"]["explained_material_id"] = material
    overview = client.json("overview", "/api/demo/operator")
    bound(overview, company, context, "overview")
    require(overview.get("available") is True and overview.get("watch_items") == [] and
            isinstance(overview.get("attention"), list) and
            len(overview.get("agents", [])) == 4, "overview", "operator_unavailable")
    receipt["attention_count"] = len(overview["attention"])
    inspect = operator(client, csrf, company, context,
                       f"Inspect Atlas Petrochem's {material} inventory and current news. State observed facts and limits.",
                       "completed", "inspect", receipt)
    require(inspect.get("proposal") is None and "atlas" in inspect["assistant_text"].lower(),
            "inspect", "inspection_not_grounded")
    explain = operator(client, csrf, company, context,
                       f"Explain observed news item {relevant['item_id']} for {material} conditionally. "
                       "Cite that item and do not assert a confirmed delay.",
                       "completed", "explain", receipt)
    require(explain.get("proposal") is None and relevant["item_id"] in explain["evidence_ids"] and
            "conditional" in explain["assistant_text"].lower(),
            "explain", "explanation_not_grounded")
    watch = operator(client, csrf, company, context,
                     f"Add a watch item for {material}. I will review before saving it.",
                     "proposal", "watch", receipt)
    proposal = proposal_check(watch.get("proposal"), "watch", context, "watch")
    require(proposal["parameters"].get("material_id") == material and
            proposal.get("plan_id") is None and proposal["offer_versions"] == {},
            "watch", "watch_not_bound")
    require(client.json("pre_apply", "/api/demo/operator").get("watch_items") == [],
            "watch", "write_before_review")
    foreign = client_factory(base)
    foreign_csrf = foreign.json("foreign_session", "/api/demo/session")["csrf_token"]
    denied_status(foreign, "foreign_watch", "/api/demo/operator/confirm",
                  {"proposal_id": proposal["proposal_id"], "reviewed": True,
                   "expected_context_version": context, "idempotency_key": key()},
                  foreign_csrf, 404)
    applied = apply_once(client, csrf, proposal, company, context, "watch", "watch_apply", receipt)
    watch_id = applied["result"].get("watch_id")
    after = client.json("post_apply", "/api/demo/operator")
    require(isinstance(watch_id, str) and len(after.get("watch_items", [])) == 1 and
            after["watch_items"][0].get("watch_id") == watch_id,
            "watch", "watch_effect_not_unique")
    denied_status(client, "stale_watch", "/api/demo/operator/confirm",
                  {"proposal_id": proposal["proposal_id"], "reviewed": True,
                   "expected_context_version": context - 1, "idempotency_key": key()}, csrf, 409)
    audio, rate = wav_input(wav)
    transcript = client.json("transcribe", "/api/demo/voice/transcribe",
                             body={"audio_base64": base64.b64encode(audio).decode("ascii"),
                                   "mime_type": "audio/wav", "sample_rate_hertz": rate,
                                   "expected_context_version": context,
                                   "idempotency_key": key()}, csrf=csrf)
    bound(transcript, company, context, "transcribe")
    require(transcript.get("provider") == "google_cloud_stt" and
            transcript.get("model") == "latest_short" and transcript.get("call") == "real" and
            isinstance(transcript.get("transcript"), str) and
            intended_inspection(transcript["transcript"]),
            "transcribe", "stt_not_intended_inspection")
    receipt["speech"] = {"stt_provider": transcript["provider"], "stt_model": transcript["model"],
                         "stt_call": "real", "transcript": transcript["transcript"],
                         "wav_sha256": hashlib.sha256(audio).hexdigest(),
                         "company_id": company, "context_version": context}
    # This is an explicit synthetic acceptance action; transcription itself does not submit a turn.
    voice_turn = operator(client, csrf, company, context, transcript["transcript"],
                          "completed", "voice_turn", receipt)
    require(voice_turn.get("proposal") is None and
            "atlas" in voice_turn["assistant_text"].lower(),
            "voice_turn", "voice_inspection_not_grounded")
    receipt["speech"]["voice_turn_id"] = voice_turn["turn_id"]
    spoken = client.json("speak", "/api/demo/voice/speak",
                         body={"text": voice_turn["assistant_text"][:1200],
                               "expected_context_version": context,
                               "idempotency_key": key()}, csrf=csrf)
    bound(spoken, company, context, "speak")
    require(spoken.get("provider") == "google_cloud_tts" and spoken.get("call") == "real" and
            spoken.get("mime_type") == "audio/mpeg", "speak", "tts_not_real")
    try:
        mp3 = base64.b64decode(spoken["audio_base64"], validate=True)
    except (KeyError, ValueError, TypeError):
        raise AcceptanceError("speak", "invalid_mp3_base64") from None
    require(0 < len(mp3) <= 750_000 and
            (mp3.startswith(b"ID3") or mp3[:1] == b"\xff"), "speak", "invalid_mp3")
    receipt["speech"].update({"tts_provider": spoken["provider"], "tts_call": "real",
                               "mp3_bytes": len(mp3), "mp3_sha256": hashlib.sha256(mp3).hexdigest()})
    if audio_output is not None:
        write_owned(audio_output, mp3)
        receipt["speech"]["audio_output"] = str(audio_output.expanduser().resolve())
    if procurement:
        selected = client.json("return_solara", "/api/demo/company",
                               body={"company_id": "solara-cables",
                                     "expected_context_version": context,
                                     "idempotency_key": key()}, csrf=csrf)
        previous_context = context
        company = selected.get("company", {}).get("company_id")
        context = selected.get("context_version")
        require(company == "solara-cables" and type(context) is int and
                context > previous_context and selected.get("offers") == [],
                "return_solara", "wrong_company_context")
        receipt["procurement_company"] = company
        receipt["procurement_context_version"] = context
        offers, plan = reviewed_plan(client, csrf, context)
        receipt["procurement_plan"] = {"plan_id": plan["plan_id"],
            "selected_offer_id": plan["selected_offer_id"], "quote_mode": "rules"}
        europa = offers["eu-copper"]["offer_id"]
        recovery = operator(client, csrf, company, context,
                            "Compare Europa's selected copper offer with an exact 20 day delay; propose a hypothetical recovery.",
                            "proposal", "recovery", receipt)
        proposed = proposal_check(recovery.get("proposal"), "recovery", context, "recovery")
        require(proposed["plan_id"] == plan["plan_id"] and
                proposed["offer_versions"] == plan["offer_versions"] and
                proposed["parameters"].get("delay_days_by_offer") == {europa: 20} and
                isinstance(proposed["parameters"].get("preview"), dict),
                "recovery", "recovery_not_bound")
        effect = apply_once(client, csrf, proposed, company, context, "recovery", "recovery_apply", receipt)
        require(effect["result"].get("baseline", {}).get("plan_id") == plan["plan_id"],
                "recovery", "wrong_baseline")
        rfq = operator(client, csrf, company, context,
                       "Prepare an unsent RFQ draft for the selected current copper purchase plan.",
                       "proposal", "rfq", receipt)
        proposed = proposal_check(rfq.get("proposal"), "rfq", context, "rfq")
        require(proposed["plan_id"] == effect["result"].get("scenario", {}).get("plan_id") and
                proposed["offer_versions"] == plan["offer_versions"],
                "rfq", "rfq_not_bound")
        draft = apply_once(client, csrf, proposed, company, context, "rfq", "rfq_apply", receipt)
        require(draft["result"].get("status") == "draft_unsent" and
                draft["result"].get("plan_id") == proposed["plan_id"],
                "rfq", "draft_not_unsent")
    require(len(receipt["turns"]) <= 6 and len(receipt["turns"]) * 3 <= 18,
            "budget", "operator_budget_exceeded")
    receipt["budgets"] = {"operator_turns": len(receipt["turns"]),
                          "reserved_model_calls": len(receipt["turns"]) * 3,
                          "speech_calls": 2,
                          "session_caps": {"operator_turns": 6, "speech_calls": 8},
                          "process_caps": {"model_calls": 60, "speech_calls": 40}}


def self_test() -> None:
    require(intended_inspection("Inspect the company inventory and explain its stock cover."),
            "self_test", "intended_voice_rejected")
    require(not intended_inspection("Watch a movie about inventory."),
            "self_test", "unrelated_voice_accepted")
    observed = {"items": [{"item_id": "public-oil", "matched_materials": ["feedstock"]}]}
    atlas = {"materials": [{"material_id": "feedstock"}]}
    solara = {"materials": [{"material_id": "copper"}]}
    item, material = matched_news(observed, atlas)
    require(item["item_id"] == "public-oil" and material == "feedstock",
            "self_test", "atlas_match_missing")
    try:
        matched_news(observed, solara)
    except AcceptanceError as exc:
        require(exc.code == "no_explainable_news_item", "self_test", "foreign_match_accepted")
    else:
        raise AcceptanceError("self_test", "foreign_match_accepted")
    proposal = {"proposal_id": "p", "kind": "watch", "summary": "Review watch",
                "parameters": {"material_id": "copper"}, "plan_id": None,
                "offer_versions": {}, "context_version": 1,
                "expires_at": "2099-01-01T00:00:00+00:00"}
    proposal_check(proposal, "watch", 1, "self_test")
    steps = [{"agent": role, "status": "completed", "tool": "fixture",
              "model": MODEL if role != "verifier" else "deterministic",
              "provider_call": "real" if role != "verifier" else "none"}
             for role in (*ROLES, "verifier")]
    turn = {"company_id": "solara-cables", "context_version": 1,
            "status": "proposal", "read_only": True, "turn_id": "t",
            "assistant_text": "Review watch", "agent_steps": steps,
            "evidence_ids": [], "proposal": proposal}
    turn_check(turn, "solara-cables", 1, "proposal", "self_test")
    steps[1]["provider_call"] = "mocked"
    try:
        turn_check(turn, "solara-cables", 1, "proposal", "self_test")
    except AcceptanceError as exc:
        require(exc.code == "model_receipt_not_real", "self_test", "mock_accepted")
    else:
        raise AcceptanceError("self_test", "mock_accepted")
    class FakeClient:
        def __init__(self):
            self.effects = 0
            self.confirm_key = None

        def json(self, stage, path, *, body, csrf):
            assert path == "/api/demo/operator/confirm" and csrf == "csrf"
            if body["reviewed"] is False:
                raise AcceptanceError(stage, "review_required", 422)
            if self.confirm_key is None:
                self.confirm_key = body["idempotency_key"]
                self.effects += 1
            assert self.confirm_key == body["idempotency_key"]
            return {"company_id": "solara-cables", "context_version": 1,
                    "status": "applied", "kind": "watch",
                    "result": {"watch_id": "w"}, "session": {}}

    fake = FakeClient()
    report = {"effects": []}
    apply_once(fake, "csrf", proposal, "solara-cables", 1, "watch", "self_test", report)
    require(fake.effects == 1 and report["effects"][0]["replay_same_result"],
            "self_test", "replay_effect_not_unique")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Exact HTTPS Cloud Run origin")
    parser.add_argument("--output", type=Path, help="New task-owned JSON receipt")
    parser.add_argument("--audio-wav", type=Path, help="Existing owned synthetic PCM16 mono WAV")
    parser.add_argument("--audio-output", type=Path, help="Optional new task-owned MP3 for playback")
    parser.add_argument("--with-procurement", action="store_true")
    parser.add_argument("--live", action="store_true", help="Permit hosted calls and provider budget")
    parser.add_argument("--self-test", action="store_true", help="Run local fake-contract checks only")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("self_test_passed")
        return 0
    if not args.live or not args.url or not args.output or not args.audio_wav:
        parser.error("--live, --url, --output and --audio-wav are required for hosted acceptance")
    receipt = {"success": False, "revision": None, "news": None, "turns": [],
               "effects": [], "speech": None, "budgets": None, "errors": []}
    try:
        run(base_url(args.url), args.audio_wav, receipt, live=True,
            procurement=args.with_procurement, audio_output=args.audio_output)
        receipt["success"] = True
    except AcceptanceError as exc:
        receipt["errors"].append({"stage": exc.stage, "code": exc.code, "status": exc.status})
    except Exception:
        receipt["errors"].append({"stage": "script", "code": "unexpected_script_error"})
    try:
        write_owned(args.output, (json.dumps(receipt, indent=2, sort_keys=True,
                                             allow_nan=False) + "\n").encode("utf-8"))
    except AcceptanceError:
        print("receipt_write_failed", file=sys.stderr)
        return 2
    print(str(args.output.expanduser().resolve()))
    return 0 if receipt["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
