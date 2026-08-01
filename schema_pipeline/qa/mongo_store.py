"""MongoDB persistence for the per-session missing-parameter matrix.

One collection, one document per session, ``_id`` = session id. Writes are
upserts, so re-running the pipeline over a delivery refreshes each session in
place rather than accumulating history -- the document always reflects the
latest validation of that session.

``pymongo`` is imported at module import time; the rest of the pipeline never
imports this module unless a Mongo target is configured, so the CLI keeps
working without the dependency installed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from pymongo import ASCENDING, DESCENDING, MongoClient, ReplaceOne
from pymongo.errors import PyMongoError

from . import missing_matrix as M

DEFAULT_DB = "qa"
DEFAULT_COLLECTION = "missing_parameters"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MissingParamStore:
    """Thin layer over one collection. Every method is safe to call repeatedly."""

    def __init__(self, uri: str, db_name: str = DEFAULT_DB,
                 collection: str = DEFAULT_COLLECTION,
                 timeout_ms: int = 10_000) -> None:
        self.client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        self.db = self.client[db_name]
        self.col = self.db[collection]

    @classmethod
    def from_env(cls, uri: Optional[str] = None, db_name: Optional[str] = None,
                 collection: Optional[str] = None) -> "MissingParamStore":
        """Explicit arguments win; anything omitted falls back to the env."""
        uri = uri or os.environ.get("MONGO_URI")
        if not uri:
            raise RuntimeError(
                "no MongoDB URI: pass --mongo-uri or set MONGO_URI "
                "(e.g. mongodb://localhost:27017)")
        return cls(uri,
                   db_name or os.environ.get("MONGO_DB", DEFAULT_DB),
                   collection or os.environ.get("MONGO_COLLECTION", DEFAULT_COLLECTION))

    def ping(self) -> None:
        """Fail fast at startup rather than after a delivery has been validated."""
        self.client.admin.command("ping")

    def ensure_indexes(self, slim: bool = False) -> None:
        # missing_parameters is the field the whole collection exists to be
        # queried on ("which sessions lack geo_location") -- multikey index.
        self.col.create_index([("missing_parameters", ASCENDING)])
        if slim:                       # the other fields are not stored
            return
        self.col.create_index([("verdict", ASCENDING)])
        self.col.create_index([("episode_uuid", ASCENDING)])
        self.col.create_index([("missing_count", DESCENDING)])
        self.col.create_index([("validated_at", DESCENDING)])

    def drop(self) -> None:
        """Discard the collection, indexes included. The reports on disk are the
        source of truth, so a reload rebuilds it exactly."""
        self.col.drop()

    # -----------------------------------------------------------------
    # Writing
    # -----------------------------------------------------------------
    def save(self, doc: Dict[str, Any], stamp: bool = True) -> str:
        doc = dict(doc)
        if stamp:
            doc["stored_at"] = _utcnow()
        self.col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return doc["_id"]

    def save_many(self, docs: Iterable[Dict[str, Any]],
                  stamp: bool = True) -> Dict[str, int]:
        """Bulk upsert. Returns {matched, modified, upserted, written}.

        ``stamp=False`` writes the documents verbatim -- used for the slim
        shape, where the document is meant to be the key and its value alone.
        """
        ops, now = [], _utcnow()
        for doc in docs:
            doc = dict(doc)
            if stamp:
                doc["stored_at"] = now
            ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
        if not ops:
            return {"matched": 0, "modified": 0, "upserted": 0, "written": 0}
        res = self.col.bulk_write(ops, ordered=False)
        return {
            "matched": res.matched_count,
            "modified": res.modified_count,
            "upserted": len(res.upserted_ids or {}),
            "written": len(ops),
        }

    # -----------------------------------------------------------------
    # Reading
    # -----------------------------------------------------------------
    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({"_id": session_id})

    def missing_parameters(self, session_id: str) -> List[str]:
        doc = self.col.find_one({"_id": session_id}, {"missing_parameters": 1})
        return list(doc["missing_parameters"]) if doc else []

    def sessions_missing(self, parameter: str) -> List[str]:
        """Session ids whose matrix marks ``parameter`` missing anywhere."""
        return [d["_id"] for d in
                self.col.find({"missing_parameters": parameter}, {"_id": 1})]

    def rollup(self, limit: int = 0) -> Dict[str, Any]:
        """Delivery-wide column totals, computed server-side."""
        pipeline: List[Dict[str, Any]] = [
            {"$unwind": "$missing_parameters"},
            {"$group": {"_id": "$missing_parameters", "sessions": {"$sum": 1}}},
            {"$sort": {"sessions": -1, "_id": 1}},
        ]
        if limit:
            pipeline.append({"$limit": limit})
        ranked = [{"parameter": d["_id"], "sessions": d["sessions"]}
                  for d in self.col.aggregate(pipeline)]
        verdicts = {d["_id"]: d["n"] for d in self.col.aggregate(
            [{"$group": {"_id": "$verdict", "n": {"$sum": 1}}}])}
        return {
            "sessions": self.col.estimated_document_count(),
            "verdicts": verdicts,
            "columns": list(M.COLUMNS),
            "missing_sessions_by_parameter": ranked,
        }

    def close(self) -> None:
        try:
            self.client.close()
        except PyMongoError:
            pass
