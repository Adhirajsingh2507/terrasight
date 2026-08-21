# Deploying TerraSight

Two deploy paths: **Vercel** (recommended) and **Docker** (self-hosted / local
prod-like run). Neither is triggered by this repo automatically — deploying
is an explicit, outward-facing action for whoever owns the Vercel project /
hosting environment.

## Architecture recap

- `frontend/` — Next.js.
- `backend/` — FastAPI (`app.main:app`), lean: installs `backend/requirements.txt`
  only. `backend/requirements-cv.txt` (numpy/opencv, on-rover perception
  tooling) is never installed in a deployed image — `app.main` doesn't import it.
- Frontend and backend communicate only through the frozen API contract
  (see root `CLAUDE.md`), over a **public** backend base URL. The frontend
  never talks to Supabase directly and never holds the service-role key.

## Vercel deploy

Vercel monorepos give each deployable app its **own Vercel project**, scoped
via `rootDirectory` (see the `vercel:deployments-cicd` skill / Vercel monorepo
docs). This repo deploys as **two Vercel projects** from one Git repo:

| Project | Root | Config | Framework |
|---|---|---|---|
| `terrasight` (frontend) | `frontend/` | root `vercel.json`: `{"rootDirectory": "frontend"}` | Next.js (auto-detected via `frontend/package.json`) |
| `terrasight-backend` | `backend/` | `backend/vercel.json` | Python / FastAPI, `app/main.py` (auto-detected via `backend/requirements.txt`) |

Both run as Vercel Functions on **Fluid Compute** (the default execution
model for all Vercel Functions today — nothing to opt into). Neither project
sets `runtime: 'edge'`: the backend is Python (edge doesn't apply), and the
frontend has no route handlers declaring an edge runtime.

### One-time setup

1. `terrasight` is already linked (`.vercel/project.json`). Set **Project
   Settings → Environment Variables**: `NEXT_PUBLIC_API_URL` = the backend
   project's deployed URL (e.g. `https://terrasight-backend.vercel.app`).
   This is a **public** value — never put `SUPABASE_KEY` here.
2. Create the backend project and link it from `backend/`:
   ```bash
   cd backend && vercel link --yes --project terrasight-backend
   ```
3. On the **backend** project only, set `SUPABASE_URL` and `SUPABASE_KEY`
   (service_role). Leave both unset to serve `backend/mock/*.json` (see
   `backend/app/db.py`) — useful for a first deploy before Supabase is wired up.
4. Push to `main` (Vercel Git integration deploys each project on push), or
   run `vercel --prod` from each project directory.

### CI-driven deploy (optional)

`.github/workflows/deploy.yml` is a **guarded, manually-triggered**
(`workflow_dispatch`) workflow: it no-ops if `VERCEL_TOKEN` isn't set as a
repo secret, so adding it doesn't break CI or fire an unwanted deploy.
Required secrets to actually use it: `VERCEL_TOKEN`, `VERCEL_ORG_ID`,
`VERCEL_PROJECT_ID_BACKEND` (frontend project id is picked up from the
committed `.vercel/project.json`).

### Note on the previous `vercel.json`

The prior root `vercel.json` used a `services` key with rewrite
`destination` objects (`{"type": "service", "service": "backend"}`). That
shape isn't part of Vercel's documented `vercel.json` schema — `rewrites[].
destination` is a string (path or absolute URL), and there is no top-level
`services` key (the real multi-app-routing feature, Microfrontends, is
configured via a separate `microfrontends.json` + `@vercel/microfrontends`,
and doesn't cover Python backends). It's been replaced with the
`rootDirectory`-per-project approach above, which matches Vercel's
documented monorepo guidance and needs no unverified config shape.

If you'd rather keep a single same-origin `/api/backend/*` path instead of
a separate backend domain, you can add a string-destination rewrite to the
frontend's `vercel.json` pointing at the backend's absolute production URL:
```json
{
  "rootDirectory": "frontend",
  "rewrites": [
    { "source": "/api/backend/:path*", "destination": "https://terrasight-backend.vercel.app/:path*" }
  ]
}
```
This is optional — `NEXT_PUBLIC_API_URL` direct-fetch (above) is the default
and doesn't require hardcoding a domain in source control.

## Docker deploy (self-hosted / local prod-like run)

```bash
docker compose up --build
```

- `backend` (`backend/Dockerfile`): `python:3.11-slim`, installs
  `requirements.txt` only, runs `uvicorn app.main:app`. Reads
  `SUPABASE_URL` / `SUPABASE_KEY` from the environment (optional — unset
  falls back to `backend/mock/*.json`, which ships in the image).
- `frontend` (`frontend/Dockerfile`): multi-stage Next.js standalone build.
  Only receives `NEXT_PUBLIC_API_URL` — a public base URL, never a secret.

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
| `NEXT_PUBLIC_API_URL` | frontend only | no | public backend base URL; the *only* backend-related var the frontend gets |

Unset `SUPABASE_URL`/`SUPABASE_KEY` ⇒ backend serves `backend/mock/*.json`
(`app/db.py`) — both Vercel and Docker deploys work without Supabase configured.

## Definition of done for a deploy change

1. `bash scripts/validate-terrasight.sh` passes.
2. Backend image installs `requirements.txt` only — no numpy/opencv/torch in
   the deployed image.
3. No `NEXT_PUBLIC_*` var (or any client-reachable config) carries
   `SUPABASE_KEY`.
4. No `runtime: 'edge'` on the FastAPI backend.
