emulator: uv run --directory apps/api python ../../scripts/emulator.py
api: cd apps/api && uv run uvicorn app.main:app --reload --reload-dir app --port 8080
web: corepack pnpm --filter @recoup/web dev
