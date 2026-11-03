"""Document store interface.

FirestoreStore talks to Firestore (emulator locally) via firebase-admin; blocking calls are
wrapped with run_in_threadpool. MemoryStore backs tests and emulator-less runs.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from starlette.concurrency import run_in_threadpool

Op = Literal["==", "!=", "<", "<=", ">", ">=", "in", "array_contains"]
Filter = tuple[str, Op, Any]
Doc = dict[str, Any]


class Store(Protocol):
    async def get(self, collection: str, doc_id: str) -> Doc | None: ...
    async def set(self, collection: str, doc_id: str, data: Doc, merge: bool = False) -> None: ...
    async def create(self, collection: str, doc_id: str, data: Doc) -> bool:
        """Atomically create; returns False if the document already exists (used for dedupe)."""
        ...

    async def add(self, collection: str, data: Doc) -> str: ...
    async def delete(self, collection: str, doc_id: str) -> None: ...
    async def query(
        self,
        collection: str,
        filters: Sequence[Filter] = (),
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[Doc]: ...


def _match(doc: Doc, f: Filter) -> bool:
    field, op, value = f
    cur: Any = doc
    for part in field.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    try:
        match op:
            case "==":
                return bool(cur == value)
            case "!=":
                return bool(cur != value)
            case "<":
                return bool(cur < value)
            case "<=":
                return bool(cur <= value)
            case ">":
                return bool(cur > value)
            case ">=":
                return bool(cur >= value)
            case "in":
                return cur in value
            case "array_contains":
                return isinstance(cur, list) and value in cur
    except TypeError:
        return False
    return False


def check_firestore_value(value: Any, path: str = "", in_array: bool = False) -> None:
    """Raise like Firestore does for values it can't store (nested arrays)."""
    if isinstance(value, list):
        if in_array:
            raise ValueError(f"Firestore cannot store nested arrays (at {path or 'root'})")
        for i, v in enumerate(value):
            check_firestore_value(v, f"{path}[{i}]", True)
    elif isinstance(value, dict):
        for k, v in value.items():
            check_firestore_value(v, f"{path}.{k}" if path else str(k), False)


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Doc]] = {}

    def _col(self, collection: str) -> dict[str, Doc]:
        return self.data.setdefault(collection, {})

    async def get(self, collection: str, doc_id: str) -> Doc | None:
        doc = self._col(collection).get(doc_id)
        return {**copy.deepcopy(doc), "id": doc_id} if doc is not None else None

    async def set(self, collection: str, doc_id: str, data: Doc, merge: bool = False) -> None:
        check_firestore_value(data)
        col = self._col(collection)
        if merge and doc_id in col:
            col[doc_id] = {**col[doc_id], **copy.deepcopy(data)}
        else:
            col[doc_id] = copy.deepcopy(data)

    async def create(self, collection: str, doc_id: str, data: Doc) -> bool:
        check_firestore_value(data)
        col = self._col(collection)
        if doc_id in col:
            return False
        col[doc_id] = copy.deepcopy(data)
        return True

    async def add(self, collection: str, data: Doc) -> str:
        doc_id = data.get("id") or uuid.uuid4().hex
        self._col(collection)[doc_id] = copy.deepcopy({**data, "id": doc_id})
        return str(doc_id)

    async def delete(self, collection: str, doc_id: str) -> None:
        self._col(collection).pop(doc_id, None)

    async def query(
        self,
        collection: str,
        filters: Sequence[Filter] = (),
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[Doc]:
        # Like Firestore's FirestoreStore.query: every result carries its document id.
        docs = [
            {**d, "id": k}
            for k, d in self._col(collection).items()
            if all(_match({**d, "id": k}, f) for f in filters)
        ]
        if order_by:
            docs.sort(key=lambda d: (d.get(order_by) is None, d.get(order_by)), reverse=descending)
        if limit is not None:
            docs = docs[:limit]
        return copy.deepcopy(docs)


class FirestoreStore:
    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        self._client: Any = None

    @property
    def _db(self) -> Any:
        """Lazily initialise firebase-admin so importing the app never needs credentials."""
        if self._client is None:
            from firebase_admin import firestore

            from .firebase import firebase_app

            self._client = firestore.client(firebase_app(self._project_id))
        return self._client

    def _query_sync(
        self,
        collection: str,
        filters: Sequence[Filter],
        order_by: str | None,
        descending: bool,
        limit: int | None,
    ) -> list[Doc]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        q: Any = self._db.collection(collection)
        for field, op, value in filters:
            q = q.where(filter=FieldFilter(field, op, value))
        if order_by:
            q = q.order_by(order_by, direction="DESCENDING" if descending else "ASCENDING")
        if limit is not None:
            q = q.limit(limit)
        return [{**s.to_dict(), "id": s.id} for s in q.stream()]

    async def get(self, collection: str, doc_id: str) -> Doc | None:
        snap = await run_in_threadpool(self._db.collection(collection).document(doc_id).get)
        if not snap.exists:
            return None
        return {**snap.to_dict(), "id": snap.id}

    async def set(self, collection: str, doc_id: str, data: Doc, merge: bool = False) -> None:
        ref = self._db.collection(collection).document(doc_id)
        await run_in_threadpool(ref.set, data, merge=merge)

    async def create(self, collection: str, doc_id: str, data: Doc) -> bool:
        from google.api_core.exceptions import AlreadyExists

        ref = self._db.collection(collection).document(doc_id)
        try:
            await run_in_threadpool(ref.create, data)
        except AlreadyExists:
            return False
        return True

    async def add(self, collection: str, data: Doc) -> str:
        doc_id = data.get("id") or uuid.uuid4().hex
        await self.set(collection, str(doc_id), {**data, "id": doc_id})
        return str(doc_id)

    async def delete(self, collection: str, doc_id: str) -> None:
        await run_in_threadpool(self._db.collection(collection).document(doc_id).delete)

    async def query(
        self,
        collection: str,
        filters: Sequence[Filter] = (),
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[Doc]:
        return await run_in_threadpool(
            self._query_sync, collection, filters, order_by, descending, limit
        )
