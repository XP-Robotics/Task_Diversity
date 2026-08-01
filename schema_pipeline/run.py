#!/usr/bin/env python3
"""CLI entry point for the episode validation pipeline (Layer B + Layer C).

    python run.py <root_dir> [--out OUTDIR] [--limit N] [--quiet]
                             [--mongo-uri URI] [--mongo-db DB] [--mongo-collection C]

Validates ARKit/iOS egocentric capture packages (PRD: Episode Validation
Pipeline, Layer A + Layer B). This build implements Layer B (metadata presence,
validity, internal consistency) and the cross-file integrity checks (Layer C),
with the ERROR/WARN/INFO/BLOCKED/CEILING/FORMAT_GAP severity model. Writes
schema_report.json + schema_report.html plus a console summary.

``root`` is either a single package directory (contains metadata.json /
manifest.json) or a delivery directory whose immediate subdirectories are
packages.

With ``--mongo-uri`` (or ``$MONGO_URI``) each session's missing parameters are
also upserted into MongoDB, keyed by session id, as a chunks x parameters matrix
(see qa/missing_matrix.py). ``store_missing.py`` does the same for a report that
was written by an earlier run.

Exit codes:
    0  no package rejected
    1  at least one package REJECT (an ERROR-severity data failure)
    2  usage / ingestion error (bad path, no packages)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List

from qa import pkg_ingest, schema_report, validate

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run.py",
        description="Layer B + C validation over ARKit/iOS egocentric capture packages.",
    )
    p.add_argument("root", help="a package directory, or a delivery directory of packages")
    p.add_argument("--out", default="qa_out", help="output directory (default: qa_out)")
    p.add_argument("--limit", type=int, default=None,
                   help="cap the number of packages validated (natural order)")
    p.add_argument("--quiet", action="store_true", help="terser console output")
    p.add_argument("--mongo-uri", default=None,
                   help="store each session's missing parameters in MongoDB "
                        "(default: $MONGO_URI; omit both to skip the DB write)")
    p.add_argument("--mongo-db", default=None,
                   help="MongoDB database (default: $MONGO_DB or 'qa')")
    p.add_argument("--mongo-collection", default=None,
                   help="MongoDB collection (default: $MONGO_COLLECTION or "
                        "'missing_parameters')")
    p.add_argument("--slim", action="store_true",
                   help="store only {_id: session id, missing_parameters: [...]} "
                        "rather than the full matrix document")
    return p.parse_args(argv)


def store_missing_parameters(result: dict, args: argparse.Namespace,
                             source: str) -> None:
    """Write the per-session missing-parameter matrices to MongoDB.

    A DB failure never invalidates a completed validation: the reports are
    already on disk, so this warns and returns rather than raising.
    """
    from qa import missing_matrix, mongo_store

    docs = missing_matrix.build_documents(result, source_uri=os.path.abspath(source))
    if args.slim:
        docs = missing_matrix.slim_documents(docs)
    store = mongo_store.MissingParamStore.from_env(
        args.mongo_uri, args.mongo_db, args.mongo_collection)
    try:
        store.ping()
        store.ensure_indexes(slim=args.slim)
        res = store.save_many(docs, stamp=not args.slim)
        print(f"MongoDB: {res['written']} session document(s) "
              f"({res['upserted']} new, {res['modified']} updated) -> "
              f"{store.db.name}.{store.col.name}")
    finally:
        store.close()


def main(argv: List[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        sessions = pkg_ingest.discover_sessions(args.root, limit=args.limit)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"error: bad root path: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 -- any ingestion failure -> usage exit
        print(f"error: ingestion failed: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if not sessions:
        print(f"error: no capture sessions found under {args.root}", file=sys.stderr)
        return EXIT_USAGE

    os.makedirs(args.out, exist_ok=True)

    if not args.quiet:
        chunks = sum(s.chunk_count for s in sessions)
        print(f"Found {len(sessions)} session(s) / {chunks} chunk(s) under {args.root}\n")

    result = validate.run(sessions, now_unix=time.time())
    schema_report.write_json(result, args.out)
    schema_report.write_html(result, args.out)
    schema_report.console_summary(result, quiet=args.quiet)

    if args.mongo_uri or os.environ.get("MONGO_URI"):
        try:
            store_missing_parameters(result, args, args.root)
        except Exception as exc:                       # noqa: BLE001
            print(f"warning: MongoDB write failed: {exc}", file=sys.stderr)

    code = EXIT_FAIL if result["aggregate"]["packages_rejected"] > 0 else EXIT_OK
    if not args.quiet:
        print(f"\nReports written to {os.path.abspath(args.out)} (exit {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())
