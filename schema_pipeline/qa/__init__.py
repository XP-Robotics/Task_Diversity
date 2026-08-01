"""QA tooling for human egocentric robotics episode deliveries (schema mode).

Per-episode metadata conformance against the reference §5 registry.

Module map (one responsibility each):
  config          - diversity limits + diversity field aliases (used by profiles)
  schema          - reference §5 field registry (Mode A)
  models          - Episode dataclass
  normalize       - raw metadata -> canonical (flatten + alias); pure
  profiles        - per-delivery unwrap + confident aliases
  ingest          - sidecar pairing + ffprobe (the only I/O module)
  s3client        - pure-stdlib SigV4 S3 client
  s3ingest        - cloud-direct ingestion from an S3 prefix
  validate        - schema conformance logic; pure
  schema_report   - schema rendering (JSON + HTML + console)
  missing_matrix  - report -> per-session missing-parameter matrix; pure
  mongo_store     - MongoDB persistence for those matrices (needs pymongo)
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
    "validate",
    "schema_report",
    "missing_matrix",
    "htmlkit",
]
