# Assignment A – Interaction Ledger & Reporting Pipeline

A Python CLI implementation of Assignment A, covering lead ingestion, event processing, duplicate handling, privacy protection, and report generation.

## Requirements

- Python 3.11 or later
- Windows PowerShell (or a compatible terminal)

## Setup – Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt

$key = python -c "import secrets; print(secrets.token_hex(32))"
$env:AU_PII_KEY = $key
```

Keep the same `AU_PII_KEY` across application runs. Store it securely and never commit it to version control. Losing the key may make encrypted PII unrecoverable.

## Generate synthetic data

```powershell
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
```

## Run the pipeline

```powershell
python -m solution.main ingest data/drop_1
python -m solution.main ingest data/drop_2
python -m solution.main report --out reports
```

## Run tests

```powershell
python -m pytest -q
```

## Reports generated

The reporting command generates:

1. `batch_summary.csv`
2. `daily_overview.csv`
3. `lead_report.csv`
4. `rejections.csv`
5. `data_quality.json`

## Design overview

SQLite is used for persistent storage. Leads are matched using a workflow ID and an HMAC digest of the canonical phone number. Sensitive PII fields are encrypted before storage.

Events are maintained in an append-only ledger, deduplicated by event ID, and retained when their corresponding leads have not yet arrived. Reports are generated from the stored lead and event information.

## Limitations

The implementation has been tested using the small synthetic dataset with seed 42. The included unit tests pass, and repeated ingestion of the same drops did not change report file hashes.

Fresh-seed integration testing and exact hidden-seed output parity have not yet been verified. Review the implementation and assignment contract before relying on it for production use.

The application uses a local SQLite database. Keep the database, encryption key, generated input data, and generated reports out of version control.