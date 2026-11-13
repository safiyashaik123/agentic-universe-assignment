# Submission

## Candidate
- **Name:** Safiya Shaik
- **Assignment chosen:** A-Interaction Ledger & Reporting Pipeline
- **Time spent:** 6 hours.

## How to run
See `README.md` for clean-clone setup and run commands. Main commands:

```powershell
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution.main ingest data/drop_1
python -m solution.main ingest data/drop_2
python -m solution.main report --out reports
pytest -q
```

## Design

SQLite is used for persistent storage. Each lead is identified using
(workflow_id, HMAC(canonical_phone)). Sensitive PII fields are encrypted
before being stored.

Events are maintained in an append-only ledger and deduplicated using
event_id. Unresolved events are retained so they can be linked when
their corresponding leads arrive later.

Reports are generated from the stored leads and events using the
workflow-specific disposition catalogue.


## Decisions & assumptions
| Decision | Why | Alternative considered |
|---|---|---|
| SQLite | No external service required; easy clean-clone run | PostgreSQL |
| HMAC-SHA256 phone digest | Deterministic matching without plaintext phone storage | Plaintext phone index |
| Fernet authenticated encryption | Protects PII fields at rest | Plaintext or unauthenticated encryption |
| Store unresolved events | Allows late-arriving leads to resolve prior orphans | Drop orphan events |

## Failure modes considered
Malformed JSON, missing event IDs, duplicate event IDs, invalid phones, invalid optional fields, duplicate leads, late leads, and repeat drop ingestion. Known limitations: review exact hidden-seed report parity, key rotation, and partial file-level recovery before production use.

## Testing
- Python 3.11 environment configured successfully.
- Synthetic data generated with seed 42 and small scale.
- Both data drops ingested successfully.
- All five reports generated.
- Included test suite: 4 passed.
- Repeat ingestion produced no report hash changes.

## Results
Data quality report:
- Lead rows processed: 2,057
- Rejected lead rows: 108
- Duplicate lead rows merged: 57
- Field warnings: 122
- Events processed: 8,745
- Malformed events: 8
- Duplicate events dropped: 237
- Unknown disposition events: 19
- Orphan events: 461

All five expected report files were generated.

## Known limitations
Fresh-seed integration testing and exact hidden-seed report parity have not been verified.

## If I had another day
Add complete integration/golden tests, optimise large-scale aggregation, improve per-file transactional checkpoints, and conduct a fresh-seed report diff.
