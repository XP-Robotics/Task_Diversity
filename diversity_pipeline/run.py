#!/usr/bin/env python3
"""CLI entry point for the egocentric-episode QA tool (diversity pipeline).

    python run.py <root_dir> [--out OUTDIR] [--profile NAME] [--limit N]
                  [--s3-layout folder|paired] [--factory-dynamic] [--quiet]

Runs whole-delivery numeric diversity QA and writes diversity_report.json +
diversity_report.html plus a console summary.

Exit codes:
    0  all diversity checks clear
    1  any diversity FAIL
    2  usage / ingestion error (bad path, no episodes)
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List

from qa import ingest, metrics, profiles, report
from qa.models import Episode

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run.py",
        description="Diversity QA over a delivery of human egocentric robotics episodes.",
    )
    p.add_argument("root",
                   help="delivery root: a local directory, or s3://[bucket/]prefix "
                        "for cloud-direct QA (no download)")
    p.add_argument("--out", default="qa_out", help="output directory (default: qa_out)")
    p.add_argument("--profile", choices=profiles.names(), default="generic",
                   help="source profile that reshapes/aliases a delivery's metadata "
                        "(default: generic)")
    p.add_argument("--limit", type=int, default=None,
                   help="cap the number of episodes ingested (S3 only; natural order)")
    p.add_argument("--s3-layout", choices=["folder", "paired"], default="folder",
                   help="S3 layout: 'folder' (per-episode <uid>/ folders) or 'paired' "
                        "(videos/ + metadata/ by stem)")
    p.add_argument("--factory-dynamic", action="store_true",
                   help="use the 2h dynamic factory cap instead of the 1h repetitive default")
    p.add_argument("--quiet", action="store_true", help="terser console output")
    return p.parse_args(argv)


def _ingest_episodes(args: argparse.Namespace, profile: profiles.SourceProfile,
                     want_video: bool) -> List[Episode]:
    """Ingest from a local directory or, when root is s3://, straight from S3."""
    if args.root.startswith("s3://"):
        from qa import s3ingest
        from qa.s3client import S3Client, parse_s3_url
        client = S3Client.from_env()
        bucket, prefix = parse_s3_url(args.root, client.default_bucket)
        if args.s3_layout == "paired":
            return s3ingest.build_episodes_paired(
                client, bucket, prefix, profile=profile,
                want_video=want_video, max_episodes=args.limit)
        return s3ingest.build_episodes(client, bucket, prefix, profile=profile,
                                       want_video=want_video, max_episodes=args.limit)
    return ingest.build_episodes(args.root, profile=profile)


def _run_diversity(episodes: List[Episode], outdir: str, factory_dynamic: bool,
                   quiet: bool, probe_workers: int = 1) -> int:
    # I/O: measure video durations (degrades gracefully); parallel for remote.
    ingest.probe_durations(episodes, max_workers=probe_workers)
    result = metrics.run(episodes, factory_dynamic=factory_dynamic)
    report.write_json(result, outdir)
    report.write_html(result, outdir)
    report.console_summary(result, quiet=quiet)
    return EXIT_FAIL if result["summary"]["any_fail"] else EXIT_OK


def main(argv: List[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    profile = profiles.get(args.profile)
    profiles.apply(profile)  # merge any profile aliases into the live registries

    try:
        episodes = _ingest_episodes(args, profile, want_video=True)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"error: bad root path: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 -- S3/env/config errors -> usage exit
        print(f"error: ingestion failed: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if not episodes:
        print(f"error: no episodes found under {args.root}", file=sys.stderr)
        return EXIT_USAGE

    os.makedirs(args.out, exist_ok=True)

    if not args.quiet:
        print(f"Found {len(episodes)} episode(s) under {args.root} "
              f"[profile: {profile.name}]\n")

    probe_workers = 24 if args.root.startswith("s3://") else 1

    code = _run_diversity(episodes, args.out, args.factory_dynamic, args.quiet,
                          probe_workers=probe_workers)

    if not args.quiet:
        print(f"\nReports written to {os.path.abspath(args.out)} (exit {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())
