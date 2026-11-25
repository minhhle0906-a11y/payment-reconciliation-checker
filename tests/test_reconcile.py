"""Unit tests for the reconciliation logic.  Run with:  pytest"""

import pandas as pd
import pytest

from generate_sample_data import PLANTED, RESOLVED_BY_ADJUSTMENT, generate
from reconcile import (
    AMOUNT_MISMATCH,
    DATE_MISMATCH,
    DUPLICATE,
    MATCHED,
    MATCHED_WITH_ADJUSTMENT,
    MISSING_IN_BANK,
    MISSING_IN_LEDGER,
    SUGGESTED_MATCH,
    match_rate,
    reconcile,
    summarize,
    unused_adjustments,
)


def make(rows):
    """Build a small DataFrame from (reference, date, amount, merchant) tuples."""
    return pd.DataFrame(rows, columns=["reference", "date", "amount", "merchant"])


def adjust(rows):
    """Build an adjustments table from (reference, amount, stakeholder, note) tuples."""
    return pd.DataFrame(rows, columns=["reference", "amount", "stakeholder", "note"])


def statuses(result):
    return list(result["status"])


# --- Basic matching -----------------------------------------------------------

def test_identical_transactions_match():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    assert statuses(reconcile(ledger, bank)) == [MATCHED]


def test_even_one_day_difference_is_a_break():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN1", "2026-11-02", 1000, "Shop A")])
    assert statuses(reconcile(ledger, bank)) == [DATE_MISMATCH]


def test_date_mismatch_shows_days_apart():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN1", "2026-11-05", 1000, "Shop A")])
    result = reconcile(ledger, bank)
    assert statuses(result) == [DATE_MISMATCH]
    assert result.loc[0, "days_difference"] == 4


# --- Amounts must match exactly ---------------------------------------------

def test_amount_mismatch_shows_amount_needed():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN1", "2026-11-01", 890, "Shop A")])
    result = reconcile(ledger, bank)
    assert statuses(result) == [AMOUNT_MISMATCH]
    assert result.loc[0, "amount_difference"] == -110
    assert "adjustment of 110" in result.loc[0, "note"]


def test_even_one_yen_difference_is_a_break():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN1", "2026-11-01", 999, "Shop A")])
    assert statuses(reconcile(ledger, bank)) == [AMOUNT_MISMATCH]


# --- Adjustments --------------------------------------------------------------

def test_adjustment_that_closes_the_gap_matches():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN1", "2026-11-01", 890, "Shop A")])
    adjustments = adjust([("TXN1", 110, "Merchant Services", "Fee deducted")])
    result = reconcile(ledger, bank, adjustments)
    assert statuses(result) == [MATCHED_WITH_ADJUSTMENT]
    assert result.loc[0, "amount_difference"] == 0
    assert "Merchant Services" in result.loc[0, "note"]


def test_several_adjustments_for_one_reference_are_added_up():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN1", "2026-11-01", 800, "Shop A")])
    adjustments = adjust([("TXN1", 150, "Team A", ""), ("TXN1", 50, "Team B", "")])
    assert statuses(reconcile(ledger, bank, adjustments)) == [MATCHED_WITH_ADJUSTMENT]


def test_partial_adjustment_keeps_the_break_open():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN1", "2026-11-01", 890, "Shop A")])
    adjustments = adjust([("TXN1", 100, "Merchant Services", "")])
    result = reconcile(ledger, bank, adjustments)
    assert statuses(result) == [AMOUNT_MISMATCH]
    assert "Remaining difference: -10" in result.loc[0, "note"]


def test_adjustment_for_unknown_reference_is_reported_as_unused():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    adjustments = adjust([("TXN999", 110, "Merchant Services", "")])
    result = reconcile(ledger, bank, adjustments)
    assert statuses(result) == [MATCHED]
    assert list(unused_adjustments(adjustments, result)["reference"]) == ["TXN999"]


def test_empty_rows_in_adjustments_are_ignored():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    adjustments = adjust([(None, None, None, None)])
    assert statuses(reconcile(ledger, bank, adjustments)) == [MATCHED]


def test_adjustment_without_amount_gives_clear_error():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN1", "2026-11-01", 890, "Shop A")])
    adjustments = adjust([("TXN1", None, "Merchant Services", "")])
    with pytest.raises(ValueError, match="reference and a numeric amount"):
        reconcile(ledger, bank, adjustments)


# --- Missing, duplicates and suggested matches --------------------------------

def test_missing_on_each_side():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("TXN2", "2026-11-10", 5000, "Shop B")])
    assert sorted(statuses(reconcile(ledger, bank))) == sorted([MISSING_IN_BANK, MISSING_IN_LEDGER])


def test_duplicate_reference_is_flagged_once():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([
        ("TXN1", "2026-11-01", 1000, "Shop A"),
        ("TXN1", "2026-11-01", 1000, "Shop A"),
    ])
    assert sorted(statuses(reconcile(ledger, bank))) == sorted([MATCHED, DUPLICATE])


def test_missing_reference_becomes_suggested_match():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("", "2026-11-01", 1000, "Shop A")])
    assert statuses(reconcile(ledger, bank)) == [SUGGESTED_MATCH]


def test_suggested_match_requires_same_date():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("", "2026-11-02", 1000, "Shop A")])
    assert sorted(statuses(reconcile(ledger, bank))) == sorted([MISSING_IN_BANK, MISSING_IN_LEDGER])


def test_suggested_match_requires_same_merchant():
    ledger = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    bank = make([("", "2026-11-01", 1000, "Shop B")])
    assert sorted(statuses(reconcile(ledger, bank))) == sorted([MISSING_IN_BANK, MISSING_IN_LEDGER])


# --- Input checks ------------------------------------------------------------

def test_column_names_are_case_insensitive_and_merchant_is_optional():
    ledger = pd.DataFrame({"Reference": ["txn1"], "Date": ["2026-11-01"], "Amount": [1000]})
    bank = pd.DataFrame({"reference": ["TXN1"], "date": ["2026-11-01"], "amount": [1000]})
    assert statuses(reconcile(ledger, bank)) == [MATCHED]


def test_missing_column_gives_clear_error():
    ledger = pd.DataFrame({"reference": ["TXN1"], "amount": [1000]})
    bank = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    with pytest.raises(ValueError, match="missing these columns: date"):
        reconcile(ledger, bank)


def test_invalid_amount_gives_row_number():
    ledger = make([("TXN1", "2026-11-01", "abc", "Shop A")])
    bank = make([("TXN1", "2026-11-01", 1000, "Shop A")])
    with pytest.raises(ValueError, match="row\\(s\\): 2"):
        reconcile(ledger, bank)


# --- Full sample data ----------------------------------------------------------

def test_sample_data_finds_every_planted_problem():
    ledger, bank, adjustments = generate(n_transactions=150, seed=42)
    result = reconcile(ledger, bank, adjustments)
    counts = summarize(result).set_index("status")["count"].to_dict()

    assert counts[AMOUNT_MISMATCH] == PLANTED["amount_mismatch"] - RESOLVED_BY_ADJUSTMENT
    assert counts[MATCHED_WITH_ADJUSTMENT] == RESOLVED_BY_ADJUSTMENT
    assert counts[DATE_MISMATCH] == PLANTED["date_mismatch"]
    assert counts[MISSING_IN_BANK] == PLANTED["missing_in_bank"]
    assert counts[MISSING_IN_LEDGER] == PLANTED["missing_in_ledger"]
    assert counts[DUPLICATE] == PLANTED["duplicate"]
    assert counts[SUGGESTED_MATCH] == PLANTED["suggested_match"]
    assert unused_adjustments(adjustments, result).empty
    assert 0 < match_rate(result) < 1
