# Deploying TerraSight

Two deploy paths: **Vercel** (recommended) and **Docker** (self-hosted / local
prod-like run). Neither is triggered by this repo automatically — deploying is
an explicit, outward-facing action for whoever owns the Vercel project /
hosting environment.

## Architecture recap

- `frontend/` — Next.js.
- `backend/` — FastAPI (`app.main:app`), lean: installs `backend/requirements.txt`
  only. `backend/requirements-cv.txt` (numpy/opencv, on-rover perception
  tooling) is never installed in a deployed image — `app.main` doesn't import it.
- Frontend and backend communicate only through the frozen API contract
  (see root `CLAUDE.md`). The frontend never talks to Supabase directly and
  never holds the service-role key.

## Vercel deploy

The linked `terrasight` project uses Vercel's **`services` framework** — one
project builds **both** apps from this monorepo, declared in the root
`vercel.json`:

```jsonc
{
  "services": {
    "frontend": { "root": "frontend", "framework": "nextjs" },
    "backend":  { "root": "backend",  "entrypoint": "app.main:app" }
  },
  "rewrites": [
    { "source": "/api/backend(/.*)?", "destination": { "type": "service", "service": "backend" } },
    { "source": "/(.*)",              "destination": { "type": "service", "service": "frontend" } }
  ]
}
```

So the frontend reaches the backend **same-origin** at `/api/backend/*` (the
rewrite routes it to the FastAPI service) — no separate backend domain or
`NEXT_PUBLIC_API_URL` needed on Vercel. The backend runs as a Python Vercel
Function on Fluid Compute; no `runtime: 'edge'` (edge doesn't apply to Python).

> The project's **Framework Preset must stay `services`** — it's what makes the
> `services` block valid. `vercel.json` must NOT contain a `rootDirectory` key
> (not a valid property; it fails schema verification), and `frontend/next.config.ts`
> must NOT hard-set `output: "standalone"` (it breaks Vercel's build trace — it's
> gated behind `NEXT_OUTPUT=standalone`, which only the Docker build sets).

### One-time setup

1. `terrasight` is already linked (`.vercel/project.json`).
2. Set `SUPABASE_URL` and `SUPABASE_KEY` (service_role) in **Project Settings →
   Environment Variables** — these feed the **backend** service only. Leave both
   unset to serve `backend/mock/*.json` (see `backend/app/db.py`) — useful for a
   first deploy before Supabase is wired up.
3. Deploy: `vercel --prod` (from the repo root), or push to `main` if Git
   integration is enabled. `vercel` (no `--prod`) makes an isolated preview.

### CI-driven deploy (optional)

`.github/workflows/deploy.yml` is a **guarded, manually-triggered**
(`workflow_dispatch`) workflow: it no-ops if `VERCEL_TOKEN` isn't set as a repo
secret, so adding it doesn't break CI or fire an unwanted deploy. Required
secrets to actually use it: `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`
(the `terrasight` project id, also in `.vercel/project.json`).

## Docker deploy (self-hosted / local prod-like run)

For containers the two apps run separately (no `services` rewrite), so the
frontend reaches the backend via `NEXT_PUBLIC_API_URL`:

```bash
docker compose up --build
```

- `backend` (`backend/Dockerfile`): `python:3.11-slim`, installs
  `requirements.txt` only, runs `uvicorn app.main:app`. Reads `SUPABASE_URL` /
  `SUPABASE_KEY` from the environment (optional — unset falls back to
  `backend/mock/*.json`, which ships in the image).
- `frontend` (`frontend/Dockerfile`): multi-stage Next.js **standalone** build
  (the Dockerfile sets `NEXT_OUTPUT=standalone`). Only receives
  `NEXT_PUBLIC_API_URL` — a public base URL, never a secret.

```bash
# .env at repo root, or export in your shell before `docker compose up`
SUPABASE_URL=...
SUPABASE_KEY=...            # backend-only; never referenced by the frontend service
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Required environment variables

| Var | Where | Secret? | Notes |
|---|---|---|---|
| `SUPABASE_URL` | backend only | no | project URL |
| `SUPABASE_KEY` | backend only | **yes** (service_role) | bypasses RLS; must never reach the frontend bundle or `NEXT_PUBLIC_*` |
| `NEXT_PUBLIC_API_URL` | frontend, **Docker only** | no | public backend base URL; on Vercel the `/api/backend` rewrite makes this unnecessary |

Unset `SUPABASE_URL`/`SUPABASE_KEY` ⇒ backend serves `backend/mock/*.json`
(`app/db.py`) — both Vercel and Docker deploys work without Supabase configured.

## Live persistence

The backend *reads* from Supabase (or mock) via `app/db.py`. To *populate*
Supabase from the perception pipeline, run the pipeline with `--persist` in an
environment where `SUPABASE_URL`/`SUPABASE_KEY` are set:

```bash
cd backend && SUPABASE_URL=... SUPABASE_KEY=... python -m app.pipeline --persist
```

It's a no-op (prints so) when Supabase is unconfigured. Re-runs are idempotent
(upsert on natural keys; boundaries replaced wholesale).

## Definition of done for a deploy change

1. `bash scripts/validate-terrasight.sh` passes.
2. Backend image installs `requirements.txt` only — no numpy/opencv/torch in the
   deployed image.
3. No `NEXT_PUBLIC_*` var (or any client-reachable config) carries `SUPABASE_KEY`.
4. No `runtime: 'edge'` on the FastAPI backend.
5. `vercel.json` keeps the `services` block, no `rootDirectory`; `next.config.ts`
   keeps `output` gated behind `NEXT_OUTPUT`.
