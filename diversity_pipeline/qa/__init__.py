"""QA tooling for human egocentric robotics episode deliveries (diversity mode).

Whole-delivery numeric diversity limits.

Module map (one responsibility each):
  config          - diversity limits + diversity field aliases
  schema          - reference §5 field registry (used by profiles)
  models          - Episode dataclass
  normalize       - raw metadata -> canonical (flatten + alias); pure
  profiles        - per-delivery unwrap + confident aliases
  ingest          - sidecar pairing + ffprobe (the only I/O module)
  s3client        - pure-stdlib SigV4 S3 client
  s3ingest        - cloud-direct ingestion from an S3 prefix
  metrics         - diversity metrics; pure
  report          - diversity rendering (JSON + HTML + console)
  htmlkit         - shared inline-CSS HTML helpers
  run             - CLI
"""

__all__ = [
    "config",
    "schema",
    "models",
    "normalize",
    "profiles",
    "ingest",
    "s3client",
    "s3ingest",
    "metrics",
    "report",
    "htmlkit",
]
