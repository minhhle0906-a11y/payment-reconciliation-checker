"""Core reconciliation logic.

Compares an internal payment ledger with a bank statement, the same way a
reconciliation system does. Amounts and dates must match exactly: there is no
tolerance, because tax amounts and exchange rates depend on the exact date.
A difference stays open as a break until someone investigates it. An amount
break is closed by recording an adjustment for the missing amount.

1. Duplicates      - a reference that appears more than once in the same file.
2. Reference match - same reference on both sides, then amount and date are checked.
                     Recorded adjustments are added to the bank amount first.
3. Suggested match - no matching reference, but amount, date and merchant are the same.
                     A person must confirm it.
4. Missing         - anything left over exists on one side only.
"""

from __future__ import annotations

from io import BytesIO

import pandas as pd

REQUIRED_COLUMNS = ["reference", "date", "amount"]
OPTIONAL_COLUMNS = ["merchant"]
ADJUSTMENT_COLUMNS = ["reference", "amount", "stakeholder", "note"]

# Statuses, in the order they are shown in reports.
MATCHED = "Matched"
MATCHED_WITH_ADJUSTMENT = "Matched with adjustment"
SUGGESTED_MATCH = "Suggested match"
AMOUNT_MISMATCH = "Amount mismatch"
DATE_MISMATCH = "Date mismatch"
MISSING_IN_BANK = "Missing in bank"
MISSING_IN_LEDGER = "Missing in ledger"
DUPLICATE = "Duplicate"

STATUS_ORDER = [
    AMOUNT_MISMATCH,
    DATE_MISMATCH,
    MISSING_IN_BANK,
    MISSING_IN_LEDGER,
    DUPLICATE,
    SUGGESTED_MATCH,
    MATCHED_WITH_ADJUSTMENT,
    MATCHED,
]
RESOLVED_STATUSES = [MATCHED, MATCHED_WITH_ADJUSTMENT]
BREAK_STATUSES = [status for status in STATUS_ORDER if status not in RESOLVED_STATUSES]

RESULT_COLUMNS = [
    "status",
    "reference_ledger",
    "reference_bank",
    "merchant",
    "date_ledger",
    "date_bank",
    "amount_ledger",
    "amount_bank",
    "adjustment",
    "amount_difference",
    "days_difference",
    "note",
]


def same_amount(a: float, b: float) -> bool:
    """Amounts must be equal. The tiny margin only absorbs floating-point noise."""
    return abs(a - b) < 0.005


# ---------------------------------------------------------------------------
# 1. Cleaning and validation
# ---------------------------------------------------------------------------
def prepare(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    """Check the columns and clean one transaction file.

    Raises ValueError with a readable message if the file cannot be used.
    """
    df = df.copy()
    df.columns = [str(col).strip().lower() for col in df.columns]

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(
            f"The {source_name} file is missing these columns: {', '.join(missing)}. "
            f"Required columns are: {', '.join(REQUIRED_COLUMNS)}."
        )

    if "merchant" not in df.columns:
        df["merchant"] = ""

    df["reference"] = df["reference"].fillna("").astype(str).str.strip().str.upper()
    df["merchant"] = df["merchant"].fillna("").astype(str).str.strip()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")

    bad_rows = df[df["date"].isna() | df["amount"].isna()]
    if not bad_rows.empty:
        # +2 turns a 0-based index into the row number a user sees in Excel
        # (one for the header, one because Excel starts counting at 1).
        row_numbers = ", ".join(str(i + 2) for i in bad_rows.index[:5])
        raise ValueError(
            f"The {source_name} file has an invalid date or amount in row(s): {row_numbers}."
        )

    return df[REQUIRED_COLUMNS + OPTIONAL_COLUMNS].reset_index(drop=True)


def prepare_adjustments(adjustments: pd.DataFrame | None) -> pd.DataFrame:
    """Clean the adjustments table. Empty rows (for example from the app's editor) are ignored."""
    if adjustments is None or adjustments.empty:
        return pd.DataFrame(columns=ADJUSTMENT_COLUMNS)

    df = adjustments.copy()
    df.columns = [str(col).strip().lower() for col in df.columns]
    for col in ["reference", "amount"]:
        if col not in df.columns:
            raise ValueError(f"The adjustments table is missing the column: {col}.")
    for col in ["stakeholder", "note"]:
        if col not in df.columns:
            df[col] = ""

    df["reference"] = df["reference"].fillna("").astype(str).str.strip().str.upper()
    df["stakeholder"] = df["stakeholder"].fillna("").astype(str).str.strip()
    df["note"] = df["note"].fillna("").astype(str).str.strip()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")

    is_empty_row = (df["reference"] == "") & df["amount"].isna()
    df = df[~is_empty_row]

    bad_rows = df[(df["reference"] == "") | df["amount"].isna()]
    if not bad_rows.empty:
        raise ValueError(
            "Every adjustment needs a reference and a numeric amount. "
            f"Check adjustment row(s): {', '.join(str(i + 1) for i in bad_rows.index[:5])}."
        )
    return df[ADJUSTMENT_COLUMNS].reset_index(drop=True)


def _split_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep the first row of each reference; return the extra copies separately."""
    has_reference = df["reference"] != ""
    is_extra_copy = has_reference & df.duplicated("reference", keep="first")
    return df[~is_extra_copy], df[is_extra_copy]


# ---------------------------------------------------------------------------
# 2. Building result rows
# ---------------------------------------------------------------------------
def _result_row(status: str, ledger=None, bank=None, adjustment: float = 0.0, note: str = "") -> dict:
    """Build one row of the result table from a ledger row and/or a bank row."""
    row = {
        "status": status,
        "reference_ledger": ledger["reference"] if ledger is not None else None,
        "reference_bank": bank["reference"] if bank is not None else None,
        "merchant": (ledger if ledger is not None else bank)["merchant"],
        "date_ledger": ledger["date"] if ledger is not None else pd.NaT,
        "date_bank": bank["date"] if bank is not None else pd.NaT,
        "amount_ledger": ledger["amount"] if ledger is not None else None,
        "amount_bank": bank["amount"] if bank is not None else None,
        "adjustment": adjustment if adjustment else None,
        "amount_difference": None,
        "days_difference": None,
        "note": note,
    }
    if ledger is not None and bank is not None:
        # Positive = bank side (plus adjustments) is higher than the ledger.
        row["amount_difference"] = bank["amount"] + adjustment - ledger["amount"]
        row["days_difference"] = (bank["date"] - ledger["date"]).days
    return row


def _amount_break_note(gap: float, has_adjustment: bool) -> str:
    """Explain an open amount break and what would close it."""
    if has_adjustment:
        return f"Adjustment does not fully close the break. Remaining difference: {gap:,.0f}"
    side = "lower" if gap < 0 else "higher"
    return (
        f"Bank is {abs(gap):,.0f} {side} than ledger. Investigate with the stakeholder, "
        f"then record an adjustment of {-gap:,.0f} to match"
    )


def _compare(ledger, bank, adjustment: float, adjustment_note: str) -> dict:
    """Compare two rows that share the same reference."""
    gap = bank["amount"] + adjustment - ledger["amount"]
    day_gap = abs((bank["date"] - ledger["date"]).days)

    if not same_amount(gap, 0):
        return _result_row(AMOUNT_MISMATCH, ledger, bank, adjustment, _amount_break_note(gap, bool(adjustment)))
    if day_gap != 0:
        return _result_row(
            DATE_MISMATCH,
            ledger,
            bank,
            adjustment,
            f"Dates are {day_gap} day(s) apart. Confirm the correct date with the stakeholder",
        )
    if adjustment:
        return _result_row(MATCHED_WITH_ADJUSTMENT, ledger, bank, adjustment, adjustment_note)
    return _result_row(MATCHED, ledger, bank)


def _suggest_matches(
    ledger_only: pd.DataFrame,
    bank_only: pd.DataFrame,
) -> tuple[list[dict], pd.DataFrame, pd.DataFrame]:
    """Pair leftover rows that have exactly the same amount, date and merchant.

    These stay as breaks ("Suggested match") because the references do not
    agree, so a person has to confirm them.
    """
    results = []
    used_bank_rows = set()
    matched_ledger_rows = set()

    for ledger_index, ledger in ledger_only.sort_values("date").iterrows():
        for bank_index, bank in bank_only.iterrows():
            if bank_index in used_bank_rows:
                continue
            same_date = bank["date"] == ledger["date"]
            same_merchant = (
                not ledger["merchant"]
                or not bank["merchant"]
                or ledger["merchant"].lower() == bank["merchant"].lower()
            )
            if same_amount(bank["amount"], ledger["amount"]) and same_date and same_merchant:
                used_bank_rows.add(bank_index)
                matched_ledger_rows.add(ledger_index)
                results.append(
                    _result_row(
                        SUGGESTED_MATCH,
                        ledger,
                        bank,
                        note="Amount, date and merchant are the same, but the reference is missing or different - please confirm",
                    )
                )
                break  # this ledger row is paired; move on to the next one

    ledger_left = ledger_only.drop(index=list(matched_ledger_rows))
    bank_left = bank_only.drop(index=list(used_bank_rows))
    return results, ledger_left, bank_left


# ---------------------------------------------------------------------------
# 3. Main entry point
# ---------------------------------------------------------------------------
def reconcile(
    ledger: pd.DataFrame,
    bank: pd.DataFrame,
    adjustments: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Reconcile a ledger against a bank statement.

    Amounts and dates must match exactly.

    Args:
        ledger: internal records, with columns reference, date, amount (and optionally merchant).
        bank: bank statement with the same columns.
        adjustments: optional table of adjustments (reference, amount, stakeholder, note).
            Each amount is added to the bank side for that reference.

    Returns:
        One row per transaction (or pair of transactions) with a status column.
    """
    ledger = prepare(ledger, "ledger")
    bank = prepare(bank, "bank")
    adjustments = prepare_adjustments(adjustments)
    results: list[dict] = []

    # Total adjustment and a combined note for each reference.
    adjustment_total: dict[str, float] = {}
    adjustment_note: dict[str, str] = {}
    for _, adj in adjustments.iterrows():
        ref = adj["reference"]
        adjustment_total[ref] = adjustment_total.get(ref, 0.0) + adj["amount"]
        detail = f"Adjustment {adj['amount']:,.0f}"
        if adj["stakeholder"]:
            detail += f" confirmed with {adj['stakeholder']}"
        if adj["note"]:
            detail += f": {adj['note']}"
        adjustment_note[ref] = "; ".join(filter(None, [adjustment_note.get(ref, ""), detail]))

    # Step 1: flag duplicates and set them aside.
    ledger, ledger_duplicates = _split_duplicates(ledger)
    bank, bank_duplicates = _split_duplicates(bank)
    for _, row in ledger_duplicates.iterrows():
        results.append(_result_row(DUPLICATE, ledger=row, note="Reference appears more than once in the ledger"))
    for _, row in bank_duplicates.iterrows():
        results.append(_result_row(DUPLICATE, bank=row, note="Reference appears more than once in the bank statement"))

    # Step 2: match on reference, including any recorded adjustments.
    ledger_refs = set(ledger.loc[ledger["reference"] != "", "reference"])
    bank_refs = set(bank.loc[bank["reference"] != "", "reference"])
    shared_refs = ledger_refs & bank_refs

    ledger_by_ref = ledger[ledger["reference"].isin(shared_refs)].set_index("reference", drop=False)
    bank_by_ref = bank[bank["reference"].isin(shared_refs)].set_index("reference", drop=False)
    for reference in sorted(shared_refs):
        results.append(
            _compare(
                ledger_by_ref.loc[reference],
                bank_by_ref.loc[reference],
                adjustment_total.get(reference, 0.0),
                adjustment_note.get(reference, ""),
            )
        )

    ledger_only = ledger[~ledger["reference"].isin(shared_refs)]
    bank_only = bank[~bank["reference"].isin(shared_refs)]

    # Step 3: suggest pairs for what is left, based on amount, date and merchant.
    suggested, ledger_only, bank_only = _suggest_matches(ledger_only, bank_only)
    results.extend(suggested)

    # Step 4: anything still left is missing on the other side.
    for _, row in ledger_only.iterrows():
        results.append(_result_row(MISSING_IN_BANK, ledger=row, note="No matching bank transaction"))
    for _, row in bank_only.iterrows():
        results.append(_result_row(MISSING_IN_LEDGER, bank=row, note="No matching ledger record"))

    result = pd.DataFrame(results, columns=RESULT_COLUMNS)
    result["status"] = pd.Categorical(result["status"], categories=STATUS_ORDER, ordered=True)
    result = result.sort_values(["status", "date_ledger", "date_bank"]).reset_index(drop=True)
    result["status"] = result["status"].astype(str)
    return result


# ---------------------------------------------------------------------------
# 4. Reporting helpers
# ---------------------------------------------------------------------------
def summarize(result: pd.DataFrame) -> pd.DataFrame:
    """Count transactions and total value per status."""
    value = result["amount_ledger"].fillna(result["amount_bank"]).abs()
    summary = (
        result.assign(value=value)
        .groupby("status")
        .agg(count=("status", "size"), total_amount=("value", "sum"))
        .reindex(STATUS_ORDER)
        .dropna(subset=["count"])
        .reset_index()
    )
    summary["count"] = summary["count"].astype(int)
    return summary


def match_rate(result: pd.DataFrame) -> float:
    """Share of result rows that are matched, with or without an adjustment (0.0 to 1.0)."""
    if result.empty:
        return 0.0
    return float(result["status"].isin(RESOLVED_STATUSES).mean())


def unused_adjustments(adjustments: pd.DataFrame | None, result: pd.DataFrame) -> pd.DataFrame:
    """Adjustments whose reference is not an open or resolved pair in the result."""
    adjustments = prepare_adjustments(adjustments)
    used = set(result.loc[result["adjustment"].notna(), "reference_ledger"])
    return adjustments[~adjustments["reference"].isin(used)]


def build_excel_report(result: pd.DataFrame, adjustments: pd.DataFrame | None = None) -> bytes:
    """Create an Excel report: summary, open breaks, adjustments and all results."""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        summarize(result).to_excel(writer, sheet_name="Summary", index=False)
        result[result["status"].isin(BREAK_STATUSES)].to_excel(
            writer, sheet_name="Open breaks", index=False
        )
        prepare_adjustments(adjustments).to_excel(writer, sheet_name="Adjustments", index=False)
        result.to_excel(writer, sheet_name="All results", index=False)
    return buffer.getvalue()
