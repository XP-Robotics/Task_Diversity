#!/usr/bin/env python3
"""Load the missing parameters out of a schema report and into MongoDB.

    python store_missing.py <schema_report.json | qa_out_dir> [options]

One document per session, ``_id`` = session id, value = the missing-parameter
matrix (rows = chunks, columns = spec parameters). See qa/missing_matrix.py for
the document shape.

This reads a report that has already been written, so reports produced by an
earlier run can be loaded without re-validating the delivery. ``run.py
--mongo-uri`` does the same thing inline at the end of a validation.

    # load an existing report
    python store_missing.py xphi4_qa_out --mongo-uri mongodb://localhost:27017

    # look at the documents without a database
    python store_missing.py xphi4_qa_out --dry-run --json /tmp/matrix.json

    # a per-video report: group its packages back into one document per session
    python store_missing.py xphi3_qa_out --session-re '^([^-]+)-' --dry-run

Exit codes: 0 ok, 2 usage / connection error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List

from qa import missing_matrix as M

EXIT_OK = 0
EXIT_USAGE = 2


def load_report(path: str) -> Dict[str, Any]:
    if os.path.isdir(path):
        path = os.path.join(path, "schema_report.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="store_missing.py",
        description="Store per-session missing parameters (as a matrix) in MongoDB.")
    p.add_argument("report", help="schema_report.json, or the output dir holding it")
    p.add_argument("--mongo-uri", default=None,
                   help="mongodb connection string (default: $MONGO_URI)")
    p.add_argument("--mongo-db", default=None, help="database (default: $MONGO_DB or 'qa')")
    p.add_argument("--mongo-collection", default=None,
                   help="collection (default: $MONGO_COLLECTION or 'missing_parameters')")
    p.add_argument("--session-re", default=None,
                   help="regex whose group(1) is the session id, for reports whose "
                        "packages are per-video (e.g. '^([^-]+)-'); packages of one "
                        "session merge into a single document, one row each")
    p.add_argument("--slim", action="store_true",
                   help="store only {_id: session id, missing_parameters: [...]} "
                        "instead of the full matrix document")
    p.add_argument("--drop", action="store_true",
                   help="drop the collection before writing (it is rebuilt from "
                        "the report, so nothing is lost)")
    p.add_argument("--dry-run", action="store_true",
                   help="build the documents but do not write to MongoDB")
    p.add_argument("--json", dest="json_out", default=None,
                   help="also write the documents to this JSON file")
    p.add_argument("--quiet", action="store_true", help="terser console output")
    return p.parse_args(argv)


def summarize(docs: List[Dict[str, Any]], quiet: bool = False) -> None:
    rollup = M.delivery_rollup(docs)
    print(f"sessions: {rollup['sessions']}")
    if not docs:
        return
    if not quiet:
        for doc in docs:
            print(f"  {doc['_id']}  [{doc['verdict']}]  "
                  f"{len(doc['rows'])}x{len(doc['columns'])} matrix, "
                  f"{doc['missing_count']} missing: "
                  f"{', '.join(doc['missing_parameters']) or '-'}")
    print("\nmissing parameters across the delivery (sessions affected):")
    for row in rollup["missing_sessions_by_parameter"]:
        print(f"  {row['sessions']:>5}  {row['parameter']}")


def main(argv: List[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        report = load_report(args.report)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read report: {exc}", file=sys.stderr)
        return EXIT_USAGE

    session_of = None
    if args.session_re:
        try:
            pattern = re.compile(args.session_re)
        except re.error as exc:
            print(f"error: bad --session-re: {exc}", file=sys.stderr)
            return EXIT_USAGE

        def session_of(package_id: str):                # noqa: F811
            m = pattern.match(package_id)
            return m.group(1) if m else None

    docs = M.build_documents(report, source_uri=os.path.abspath(args.report),
                             session_of=session_of)
    if not docs:
        print("error: report contains no sessions", file=sys.stderr)
        return EXIT_USAGE

    # The rollup below reads the full documents, so keep them and slim a copy.
    to_store = M.slim_documents(docs) if args.slim else docs

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(to_store, fh, indent=2, ensure_ascii=False)
        print(f"wrote {len(to_store)} document(s) to {args.json_out}")

    if not args.dry_run:
        try:
            from qa.mongo_store import MissingParamStore
        except ImportError as exc:
            print(f"error: pymongo is required to write to MongoDB ({exc}); "
                  f"pip install 'pymongo>=4.6', or use --dry-run", file=sys.stderr)
            return EXIT_USAGE
        try:
            store = MissingParamStore.from_env(args.mongo_uri, args.mongo_db,
                                               args.mongo_collection)
            store.ping()
            store.ensure_indexes(slim=args.slim)
        except Exception as exc:                      # noqa: BLE001
            print(f"error: MongoDB unavailable: {exc}", file=sys.stderr)
            return EXIT_USAGE
        try:
            if args.drop:
                store.drop()
                store.ensure_indexes(slim=args.slim)
            res = store.save_many(to_store, stamp=not args.slim)
            print(f"mongo: wrote {res['written']} "
                  f"{'slim ' if args.slim else ''}session document(s) "
                  f"({res['upserted']} new, {res['modified']} updated) to "
                  f"{store.db.name}.{store.col.name}")
        finally:
            store.close()

    summarize(docs, quiet=args.quiet)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
