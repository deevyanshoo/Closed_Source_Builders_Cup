# Multi-agent Operator and Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Begin implementation after company/news stage is deployed and verified.

**Goal:** Operate the actual hosted company/news/procurement tools through distinct Gemini specialists and short Google voice turns.

**Architecture:** Extend the same hosted app. A real Gemini intake/coordinator delegates evidence/exposure and procurement specialists against one trusted snapshot; deterministic tools own calculations and a verifier validates evidence references/action arguments. The backend alone commits a reviewed current proposal. Voice transcribes to the same editable text tool workflow and speaks the verified answer.

**Tech Stack:** Existing google-genai SDK/Vertex, FastAPI/React, Google Speech-to-Text/Text-to-Speech REST via server ADC. Explicit specialized prompts and tool contracts implement bounded multi-agent orchestration without requiring another provider/framework.

**Spec:** docs/superpowers/specs/2026-10-14-company-news-operator.md

## Constraints and review focus

- No real RFQ send, order placement, synthetic/live confusion, private uploads or browser credentials.
- Observed headlines are untrusted data; allowed source IDs and public URLs come from the snapshot, not the model.
- Every operation binds session/company/context/offer/plan versions. Company switch, reset, expiry, cancellation and local dirty inputs prevent old action execution.
- Actual independent specialist calls are recorded; tool/rule validation is labelled separately from model calls. Missing provider evidence returns unavailable.
- Money/stock coverage/feasibility come from Decimal tools, never model arithmetic. Critical number ambiguity requests clarification.
- Limit message 1200 chars; audio 500000 bytes/20 seconds; synthesis text 1200 chars; model 3 calls/turn, 6 turns/session and 60 operator calls/process, concurrency 2; speech 8 calls/session/40 process. Fixed provider endpoints and bounded timeouts.
- Gemini 3.5 Flash-Lite was verified with the deployed identity for strict quote extraction and is now the quote/operator model. Keep original source/schema validation and prior image rollback.

## Wire contracts

GET /api/demo/operator -> {company_id,context_version,available,agents:[{name,role}],watch_items:[],attention:[],limitations:[]}.
POST /api/demo/operator {message,expected_context_version,idempotency_key} -> OperatorTurn = {turn_id,company_id,context_version,status:'completed'|'proposal'|'needs_clarification'|'unavailable',assistant_text,agent_steps:[{agent,role,status,model,provider_call,tool,summary}],evidence_ids:string[],proposal:OperatorProposal|null,read_only:true}. Actual roles: coordinator/intake, evidence/exposure specialist, procurement specialist, deterministic verifier/tool execution. Proposal is server-created: {proposal_id,kind:'watch'|'recovery'|'rfq',summary,parameters:object,plan_id:string|null,offer_versions:object,context_version,expires_at}. Agent may suggest only server-validated identifiers/parameters; server chooses IDs/versions/time.
POST /api/demo/operator/confirm {proposal_id,reviewed:true,expected_context_version,idempotency_key} -> {status:'applied',company_id,context_version,kind,result:object,session:DemoSession}. Result is actual saved watch item, RecoveryView or RfqView. Exactly one effect under repeated request. Stale/foreign/expired proposal rejected. A successful acknowledgement alone is not a provider or action receipt.

POST /api/demo/voice/transcribe {audio_base64,mime_type:'audio/webm'|'audio/wav',sample_rate_hertz:integer,expected_context_version,idempotency_key} -> {transcript,provider,model,call:'real',company_id,context_version}. Fixed STT endpoint or explicitly identified verified Vertex audio fallback; no guessed transcript. MIME/header/byte/rate bounds, provider silence/malformed/error are rejected. Audio remains transient, never stored in sessions/logs.
POST /api/demo/voice/speak {text,expected_context_version,idempotency_key} -> {audio_base64,mime_type:'audio/mpeg',provider:'google_cloud_tts',call:'real',company_id,context_version}. Server ADC; provider limits and bounded MP3 validation.

## Ownership and interfaces

Backend Antigravity worker owns demo/operator.py, app.py, sessions.py, test_demo_operator.py and test_demo_operator_app.py. Module uses injected provider(role,prompt)->strict structured dict and trusted snapshot of company, news, current offers/plan/recovery. Default provider uses actual Vertex client, configurable model, timeout and response validation; standalone tests use fakes with explicit no actual-call claim. Internal propose returns structured summary/evidence/validated parameters; backend retains immutable proposal and commits watch/recovery/RFQ via existing modules after current state check. Per-company sessions save operator/watch state; clear proposals on quote/plan changes. Backend injects voice_service in create_app for tests, adds voice endpoints with explicit context/async guards and accounting.

Frontend Antigravity worker owns apps/web/src/demo-operator.tsx, demo-operator.test.tsx, demo-api.ts/tests, demo-page.tsx/tests and demo.css. Add operator tab/panel, distinct agent activity, attention/watch list, transcript/edit/send, proposal details/review/apply and actual outcome. Responses carry captured session/context/epoch, no late playback or state. MediaRecorder on explicit gesture, supported WebM/Opus, actual track sample rate, stop/cleanup at20s/500KB, browser microphone error states, playable returned Google MP3. Never auto-send transcription or automatically approve a proposal. Synthesis text is concise verified returned summary.

Independent voice worker owns demo/voice.py and test_demo_voice.py only. Exposes VoiceService(*,transport=None,offline=None), .transcribe(audio:bytes,mime_type:str,sample_rate_hertz:int)->dict and .speak(text:str)->dict. Server ADC fixed Google endpoints, requests AuthorizedSession or credential-backed request without printing tokens, no environment credentials/client keys. Validate codec/header/size and response types/base64/output caps. Google STT roles may need scoped permission; show explicit unavailable on denial and root decides verified Vertex fallback or concrete role approval. No cloud mutations by worker.

Root owns deployment/config/docs/provider probes, quotas approval and integration. Independent reviewer owns report/probes only.

## Tasks

1. [x] Backend: failing intent/evidence/inventory/proposal/isolation/stale/replay tests; implement model-backed roles plus authoritative tools and endpoint state.
2. [x] Voice module: failing codec/limits/offline/provider/error/transcript/audio tests; implement fixed Google REST with injected test transport.
3. [x] Frontend: failing captured-session/cancel/dirty-input/review action tests; implement actual tool/voice interface and full web gates.
4. [x] Root: independent review, combined gates, durable model probe and synthetic audio provider check, explicit package/cloud release, actual Chrome typed action + voice/transcript/playback path verification, provider receipts and rollback.

Release verification completed on Cloud Run revision `closed-source-quote-demo-00009-s8j` with deployed source `53050e22120aa6fffabbf3e8ae2ed299f6b512fe`. All ten companies/news/FX and original live Gemini quote smoke passed. Six operator turns verified reviewed watch/recovery/unsent RFQ and real Google STT-to-agent-to-TTS. Chrome separately exercised an owned synthetic MediaStream through actual MediaRecorder, Google transcription, editable unsent text, explicit operator submission, three real Gemini roles and decoded Google MP3 playback. No physical microphone audio was recorded; temporary browser test overrides were removed. Local gates: 151 demo backend checks, 251 web tests with six existing skips, session-race check, typecheck and build. Independent reported findings are closed. Runtime access/IAM scope is unchanged.
