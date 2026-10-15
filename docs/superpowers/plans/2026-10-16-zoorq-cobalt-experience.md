# ZOORQ white, ink and cobalt implementation plan

> For agentic workers: use subagent-driven-development with disjoint write ownership, coordinator integration, independent review and hosted acceptance.

**Goal:** Implement the user-approved bright enterprise design, persistent navigation, purposeful motion and prominent voice experience, then test all ten companies and deploy the existing GCP demo.
**Architecture:** Preserve the persistent React controller and authoritative server state. Replace the visual shell and leaf presentations; a scope-bound voice request opens the existing single LiveVoice instance. Keep native scrolling, compact controls and responsive reading surfaces.
**Tech stack:** React 19, TypeScript, Vite, plain CSS, existing Thinking Orbs, GSAP and Radix icons. Self-host Geist with its license. No backend or schema change is planned.
**Spec:** User-approved in-chat design dated 16 October 2026 and four concepts in task output/zoorq-design-v2-20261016. These are appearance references only; all production data must be current server values. Explicit review refinements: display any uncovered stock/lead gap accurately, distinguish proposal/preparing/preview/applied, preserve source provenance, mobile controls stay reachable.

## Global constraints

- White #FFFFFF, cool canvas #F7F9FC, ink #142033, cobalt #2159D6, secondary #526174, line #DCE3EC. Sans typography throughout; no serif/beige/coral overrides.
- Preserve company/session/context/epoch/CSRF guards, current consent, dirty-state gating, reviewed economics, stale-response rejection, native file clearing and saved drafts. No real email/order delivery.
- Scope-bound global Talk to ZOORQ opens the existing voice instance in one action. Never create another socket/microphone from a decorative orb. Voice has a real state-driven spatial lens and clear Mute/End/text controls; Thinking Orbs remains for compact inline AI status.
- Desktop rail and header remain visible while content scrolls. Mobile bottom navigation includes all five destinations with labels; header retains company/hub and Talk controls. First news story target top <320px at desktop 1200x664 after ordinary ready state.
- Sources, article limitations and stale/partial status remain available. No invented news, timestamps, savings or confidence numbers; no fixture text copied from concepts.
- Honor reduced motion; pause animation when hidden/unmounted. No scroll hijacking in news/forms. Motion must not move focus or controls after interaction.
- One coordinator owns demo-page.tsx, demo.css, dependencies/fonts, Git, browser, cloud and release. Workers write only assigned files and never commit/push/deploy/provider-mutate.
- Existing unupgraded trial credits only, same service/project/IAM/caps. Verify authenticated trial before deployment. Preserve rollback image and original user browser state; QA uses isolated sessions.

## Review focus

1. Hub/company changes cannot reuse a previous voice request or current approval; navigation preserves work without leaving microphone/socket alive after exit.
2. Persistent shell must fit 1200x664, 1440x900 and 390x844 without obscuring controls, horizontal page overflow, or trapping keyboard focus.
3. News article selection preserves list position; compact coverage does not hide stale/error warnings or source clocks.
4. Spatial animation uses actual voice state and reduced motion; current consent remains visible and spoken confirmation stays on the same connection.
5. Stock and supplier lead comparison uses matching material records and explicit unavailable/rounding states. Draft/plan controls remain blocked when dirty or unreviewed.

## Task 1: Shell, hub, overview and shared spatial lens (chat worker)

Owned files: zoorq-workspace.tsx, new zoorq-workspace.css, zoorq-lens.tsx/.css, zoorq-icons.tsx, zoorq-workspace.test.tsx and lens tests only.
Interfaces: preserve existing exports. WorkspaceNavigation accepts optional company and disabled; add WorkspaceHeader({company?,view,onTalk,onChat,disabled}). WorkspaceOverview gains optional onTalk; preserve onView, quoteCount/news. Export ZoorqLens({state?,compact?,className?}) with accessible state handled by consumer; visual only, no microphone/provider effects. Icon exports use installed @radix-ui/react-icons.
- [ ] Write meaningful failing tests for hub search, nav availability/active states, onTalk, and material-specific gap/unavailable states.
- [ ] Build fixed desktop rail/mobile bottom bar, compact header, searchable company index, dominant supply attention and voice invitation. Use real company stock/lead metadata, label it profile lead (never accepted quote lead unless actually provided).
- [ ] Build a refined cobalt/silver spatial lens using CSS 3D transforms; no bitmap screenshot as interface. GSAP entrance/transition scoped and reverted; reduced motion/static fallback.
- [ ] Run focused tests/typecheck and report diff, exact results and risks. Do not modify demo-page integration/global CSS.

## Task 2: Compact news inbox (Antigravity worker)

Owned files: demo-company-news.tsx, demo-company-news.css, demo-company-news.test.tsx only.
Interfaces: preserve exports, props, ranking/filter semantics and source clocks; consume CSS variables from shell with sensible white/ink/cobalt fallbacks.
- [ ] Add failing behavior assertions for compact controls, useful empty recovery and accessible company/global reading.
- [ ] Implement clean list/detail hierarchy, single toolbar and compact coverage, strong selected story, concise impact/next-check presentation and mobile Back. Never hide actual errors/staleness.
- [ ] Run focused tests and report. No dependency edits, no common shell/voice edits.

## Task 3: Prominent operator and scope-bound voice (Antigravity worker)

Owned files: demo-operator.tsx, demo-assistant.css, demo-operator.test.tsx, demo-live-voice.tsx and demo-live-voice.test.tsx only.
Interfaces: add exported OperatorVoiceRequest={id:number;scope:OperatorScope;returnFocusId?:string}; OperatorPanel optional voiceRequest. Consume ZoorqLens from ./zoorq-lens (provided by Task1). Root will issue scoped requests and open existing dock. Keep all old props and single mounted LiveVoice.
- [ ] Write failing tests for valid/stale voice request handling, global-entry return focus, single instance and current-consent lifecycle.
- [ ] Implement spacious white voice surface, readable captions/source/activity hierarchy and persistent controls; 44px touch targets. Reflect real connecting/listening/thinking/speaking/muted/error state. Voice starts once per current request and never on stale remount. Preserve native media disposal and same-socket spoken research.
- [ ] Polish chat dock/composer and visible source rows. No extra approval for read-only analytics, no imaginary completed action. Keep current review/consent visible.
- [ ] Run focused tests/typecheck, report exact outcomes and unresolved integration dependencies.

## Task 4: Coordinator integration and procurement craft

Owned files: demo-page.tsx, demo.css, demo-page.test.tsx, index.html, package files/public fonts, local QA and release docs.
- [ ] Reconcile fresh baseline (377 web pass/6 existing skips); capture cloud revision/caps. Reuse clean dedicated feature checkout and record ownership.
- [ ] Add failing integration tests for direct Talk request, hub exit/company stale protection and persistent-shell/procurement semantics.
- [ ] Integrate rail/header and scoped entry, preserve controller/hidden news state; remove duplicated masthead/heading and obsolete floating-only entry. Keep old accessible affordances where useful.
- [ ] Replace legacy global visual layers with coherent tokens/Geist/controls/tables/procurement stepper. Keep review, comparison, recovery and draft recap accessible and visually intentional.
- [ ] Check task diffs, run full web test/typecheck/build and appropriate demo backend suite; independently review combined diff. Fix demonstrated issues and inspect actual renders, not image references alone.

## Task 5: Hosted release and all-company acceptance

- [ ] Inspect local desktop/mobile hub, overview, news, procurement, chat and voice states plus keyboard/reduced motion. Check all ten company switches/routes on desktop/mobile and text/action/voice QA.
- [ ] Verify trial remains unupgraded; freeze allowlisted source/image and deploy only existing service with unchanged caps/IAM. Confirm exact ready revision on both URLs.
- [ ] Run all-ten-company news, procurement/text actions and controlled speech on final revision; browser direct Talk, news question, visible research consent, spoken confirmation, brief result/sources and End/focus disposal. Distinguish physical mic from controlled speech.
- [ ] Restore original browser state, record rollback/evidence, stop own preview, commit/push with correct identity and update focused PR118. Final Git clean and exact hosted result.
