# Local foundation packaging (prepared, unbuilt)

These Dockerfiles describe two separate processes. Build from the repository root with
`-f deploy/local-foundation/Dockerfile.gateway` or
`-f deploy/local-foundation/Dockerfile.python`. Their adjacent Dockerfile-specific
ignore files exclude credentials, local data, virtual environments, and generated
assets from the build context. The gateway build produces React assets and serves
them through Go. The Python image runs only `procure.service_app`, one worker.
No image has been built, pulled, run, or deployed as part of this package.

## Local development

Use the existing loopback launch path. `GATEWAY_MODE=local` accepts only a
loopback `GATEWAY_ADDR` and loopback `PYTHON_SERVICE_URL`; the development
identity file stays local. `PROCURE_SERVICE_MODE=local` validates an existing
cache at `PROCURE_DB_PATH` (or `PROCURE_DATA_DIR/app.duckdb`) and does not fetch
or create one. The Vite proxy is local development only. These container files
are not a substitute for that path.

## Private machine staging inputs

Set `GATEWAY_MODE=private_machine`, a valid `PORT`, `GATEWAY_AUDIENCE` (exact
HTTPS gateway origin), `PYTHON_SERVICE_URL` (exact HTTPS Python origin),
`PYTHON_SERVICE_AUDIENCE` equal to that Python origin, and `WEB_ASSETS_DIR`
(already `/app/web` in the gateway image). Point `MACHINE_IDENTITIES_FILE`
at a restricted regular JSON file with `principals` entries containing the
verified service-account `subject`, `email`, `principal_id`, `workspace_id`,
and `role` (`editor` or `viewer`). Point `DATABASE_URL_FILE` at a restricted
server-only PostgreSQL URL file. Do not set `DEV_IDENTITIES_FILE`,
`DEV_ALLOWED_ORIGIN`, or `GATEWAY_ADDR` in this mode. Secret/identity files must
be readable by the gateway runtime UID 65532 and not by group or other users.
The gateway must be given its own Google service identity with permission to
request an audience-bound ID token for Python; actual IAM and token issuance
remain unverified.

Set `PROCURE_SERVICE_MODE=private_machine`, `PORT`,
`PROCURE_SERVICE_AUDIENCE` (the exact Python HTTPS origin), and
`PROCURE_SERVICE_CALLERS_FILE` (restricted JSON `callers` entries with
allowlisted service-account `subject` and `email`). Mount a trusted immutable
snapshot directory as `PROCURE_BOOTSTRAP_DIR`; set `PROCURE_BOOTSTRAP_SHA256`
to its independent pinned lowercase digest. Set `PROCURE_DATA_DIR` to a
separate task-owned writable directory. The Python runtime UID is 10001;
mounts must grant it read access to the snapshot/caller file and write access
to the mutable directory. No snapshot or credential is baked into either image.

At each Python process start, source digest and schema are validated before
copying `app.duckdb` into `PROCURE_DATA_DIR/app.duckdb`. This is one rebuildable
mutable cache per process, including ephemeral Python recommendation data.
Gateway PostgreSQL is the authoritative saved plan, action, and audit store;
it is separate from that cache. Do not treat multiple Python instances or a
restarted cache as durable shared recommendation state.

Private-machine sessions are authenticated **service callers**, not human
sign-in. Ordinary browsers cannot present the service identity; the UI must
show sign-in/session unavailable. Production assets can be checked on local
loopback, but that does not demonstrate browser access to a private service.
Mumbai is the intended region, not an observed deployment. Provider/IAM,
Cloud SQL, image, and live cloud behavior have not been proved here.

## Offline snapshot export

Use an **already-existing, task-owned** compatible DuckDB cache. Do not use a
live contributor baseline, run ingestion or evaluation, or put the snapshot
inside the source cache directory. The output directory must not exist. Give
every configured material with a repeated `--material`; validation rejects a
source missing any material's prices, selection, or selected-model metrics.
The example values below are placeholders, not a runnable provider setup:

```sh
python procurement-engine/tools/cache_snapshot.py \
  --source /absolute/task-owned/existing/app.duckdb \
  --output /absolute/separate/snapshots/revision-one \
  --source-revision 0123456789abcdef0123456789abcdef01234567 \
  --synthetic true --sample false \
  --material copper --material aluminium
```

The tool writes only `app.duckdb` and `manifest.json` in a new directory,
prints its SHA-256, and leaves the source unchanged. It rejects symlinks,
source/output overlap, incompatible DuckDB data, and an existing output.
Record the printed digest independently and set the bootstrap pin to that
value; the manifest's self-reported digest alone is insufficient. Keep the
snapshot separate and immutable under operator control. No automatic cache
discovery, provider fetch, SDK call, or account operation occurs.
