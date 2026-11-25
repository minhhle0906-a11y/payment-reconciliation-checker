# Payment Reconciliation Checker

A small web app that compares an internal payment ledger with a bank statement, flags every break that needs review, and lets the user record the adjustments that resolve them.

**Live demo:** _add your Streamlit link here_

## Why I built this

I work in payment data reconciliation at a bank. Every day I match transactions in a reconciliation system, and when amounts do not agree, the items stay unmatched until I trace the stakeholders, find the reason, and post the missing amount so they can be matched. I built this project to understand how that matching logic works behind the scenes, and to practise turning an operations process into a working tool with Python.

All data in this project is synthetic. It contains no real customer or company data.

## Features

- Upload two CSV files (ledger and bank statement), or use the built-in sample data
- **Amounts and dates must match exactly**: any difference stays open as a break
- Each amount break shows the exact amount needed to close it
- Record adjustments with the stakeholder who confirmed the reason and an investigation note; a break is only matched when the difference becomes zero
- Key numbers: match rate, open breaks and their total value
- Filterable list of open breaks
- Download open breaks as CSV, or a full Excel report (Summary, Open breaks, Adjustments, All results)
- Clear error messages for missing columns, invalid values and adjustments that do not belong to any break

## How matching works

| Step | Rule | Status |
|---|---|---|
| 1 | The same reference appears more than once in one file | **Duplicate** |
| 2 | Same reference on both sides, amounts not exactly equal (after adjustments) | **Amount mismatch** |
| 2 | Same reference, dates are not the same | **Date mismatch** |
| 2 | Same reference, an adjustment closed the difference exactly | **Matched with adjustment** |
| 2 | Same reference, amount and date exactly equal | **Matched** |
| 3 | No shared reference, but amount, date and merchant are the same | **Suggested match** (must be confirmed) |
| 4 | Found on one side only | **Missing in bank** / **Missing in ledger** |

### Why no tolerance?

In finance, every difference must be explained. The amount has to be exact, and so does the date, because tax amounts and exchange rates are set by date: the same transaction booked on a different day can use a different rate. So the app never "almost matches": a break stays open until someone investigates it.

### Resolving an amount break

1. The break shows, for example: *Bank is 330 lower than ledger. Record an adjustment of 330 to match.*
2. Investigate the reason with the stakeholder (for example, a fee deducted by the acquirer).
3. Record the adjustment in the **Adjustments** tab: reference, amount, stakeholder and note.
4. The adjustment is added to the bank side. If the difference becomes exactly zero, the status changes to **Matched with adjustment**, and the note is kept as an audit trail. If not, the break stays open and shows the remaining difference.

## Input format

Ledger and bank files use the same columns. Column names are not case-sensitive.

| Column | Required | Example |
|---|---|---|
| reference | Yes | TXN000123 |
| date | Yes | 2026-11-05 |
| amount | Yes | 12500 |
| merchant | No | Sakura Cafe |

Adjustments (optional CSV, or entered in the app):

| Column | Required | Example |
|---|---|---|
| reference | Yes | TXN000071 |
| amount | Yes | 330 |
| stakeholder | No | Merchant Services |
| note | No | Acquirer fee deducted from settlement |

## Tech stack

- **Python** and **pandas** for data cleaning and matching
- **Streamlit** for the web interface
- **openpyxl** for the Excel report
- **pytest** for unit tests

## Project structure

```
payment-reconciliation-checker/
├── app.py                    # Streamlit web app (user interface)
├── reconcile.py              # Matching logic, adjustments and reports
├── generate_sample_data.py   # Creates synthetic test data with known problems
├── data/
│   ├── ledger_sample.csv
│   ├── bank_sample.csv
│   └── adjustments_sample.csv
├── tests/
│   └── test_reconcile.py     # Unit tests for each matching rule
├── requirements.txt
└── requirements-dev.txt
```

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
streamlit run app.py
```

The app opens at http://localhost:8501.

To create new sample data: `python generate_sample_data.py`

## Run tests

```bash
pytest
```

## Future improvements

- Record date corrections after a date mismatch has been investigated
- One-to-many matching, such as one bank settlement covering several payments
- Confirm or reject suggested matches in the app
- Maker-checker approval: a second person approves each adjustment
- Save results and adjustments to a database to track how long breaks stay open
- Let users map their own column names (for example, "Txn ID" to reference)
