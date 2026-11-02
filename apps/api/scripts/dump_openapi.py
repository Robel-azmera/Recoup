"""Write the API's OpenAPI schema to packages/shared/openapi.json (input to `pnpm gen:types`)."""

import json
import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("STORE_BACKEND", "memory")

from app.main import create_app  # noqa: E402

out = API_DIR.parents[1] / "packages" / "shared" / "openapi.json"
out.write_text(
    json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(f"wrote {out}")
