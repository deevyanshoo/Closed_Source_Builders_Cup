# Hosted synthetic quote-to-plan demo

The isolated `procure.demo.app` serves React `/demo` and same-origin session API. It does not change the private-machine Go gateway. Documents and economics are labelled synthetic, with optional live Vertex Gemini extraction and document-field evidence. The planner compares single-offer feasibility/cost under entered assumptions, not market timing, real procurement or a multi-period/global optimizer.

Runtime is one Cloud Run instance, one Uvicorn process, max 8 concurrent requests; ephemeral sessions last one hour. Instance restart clears state and requires a fresh session. Global/per-session call and request bounds, exact fixture hashes, body limits, CSRF, origin, ownership, version and idempotency gates protect the public demo. Only synthetic bundled documents can be selected/uploaded; keys/tokens never enter client code. Repeated immutable extracted documents may be cached, explicitly labelled in browser and export.

Docker build requires reviewed production assets at apps/web/dist. Package only Python source, these deployment files, materials/assumptions YAML and the static ISO country lookup CSV, the synthetic demo documents, and reviewed web assets. Never include credentials, local virtual environments, data/raw, forecast/evaluation/work artifacts, logs or Git databases.

Cloud runtime uses `closed-source-demo@closed-source-builder-261007.iam.gserviceaccount.com`, granted the existing custom role with only aiplatform.endpoints.predict and serviceusage.services.use. Vertex project closed-source-builder-261007, location global. Set GOOGLE_GENAI_USE_VERTEXAI=true, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, RUN_LIVE_GEMINI=1, MAX_LIVE_CALLS=60 and OFFLINE=0. Cloud Run public invocation is limited to this new synthetic service; previous services and project roles remain unchanged.

Local checks use credential-free demo tests; live provider tests are separate coordinator-owned diagnostics. Deployment is complete only after cloud revision/URL and actual fresh-browser quote correction/save/plan/export are observed. Public repo publication and competition submission are separate actions.
