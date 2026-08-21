# TerraSight API — lean deployment image.
#
# Installs requirements.txt ONLY (fastapi, uvicorn, supabase). Never
# requirements-cv.txt (numpy/opencv/torch) — those are offline/on-rover
# perception tooling that app.main never imports. See root CLAUDE.md.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only what app.main needs at runtime: the API package + its mock-JSON
# fallback (served when SUPABASE_URL/SUPABASE_KEY are unset — see app/db.py).
COPY app ./app
COPY mock ./mock

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
