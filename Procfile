emulator: firebase emulators:start --only firestore,auth --project demo-recoup
api: cd apps/api && uv run uvicorn app.main:app --reload --port 8080
web: corepack pnpm --filter @recoup/web dev
