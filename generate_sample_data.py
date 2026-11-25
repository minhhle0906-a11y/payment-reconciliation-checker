"""Create synthetic sample data for the app.

All transactions are made up. The script builds an internal ledger, copies it
into a bank statement, and then plants a known number of problems so the
reconciliation results can be checked. It also records adjustments for some
of the amount breaks, as if they had already been investigated.

Run:  python generate_sample_data.py
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

MERCHANTS = [
    "Sakura Cafe",
    "Kita Books",
    "Hikari Electronics",
    "Midori Supermarket",
    "Umi Sushi",
    "Tsubasa Travel",
    "Hoshi Pharmacy",
    "Yama Outdoor",
]

# How many of each problem to plant in the bank statement.
PLANTED = {
    "amount_mismatch": 5,
    "date_mismatch": 3,
    "missing_in_bank": 4,
    "missing_in_ledger": 3,
    "duplicate": 2,
    "suggested_match": 3,
}

# How many of the amount mismatches already have an adjustment recorded.
RESOLVED_BY_ADJUSTMENT = 2

SAMPLE_INVESTIGATIONS = [
    ("Merchant Services", "Acquirer fee deducted from settlement; fee entry posted"),
    ("Card Operations", "Partial refund settled separately; refund entry posted"),
    ("Finance", "Rounding difference on FX conversion; correction posted"),
]


def generate(
    n_transactions: int = 150, seed: int = 42
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (ledger, bank, adjustments) DataFrames with planted reconciliation breaks."""
    rng = random.Random(seed)
    start = date(2026, 11, 1)

    ledger = []
    for i in range(1, n_transactions + 1):
        ledger.append(
            {
                "reference": f"TXN{i:06d}",
                "date": start + timedelta(days=rng.randint(0, 27)),
                "amount": rng.randint(50, 5000) * 10,  # 500 to 50,000 JPY
                "merchant": rng.choice(MERCHANTS),
            }
        )

    # A correct bank record has exactly the same reference, date and amount.
    bank = [dict(txn) for txn in ledger]

    # Pick distinct transactions to break, so problems do not overlap.
    targets = rng.sample(range(n_transactions), sum(PLANTED.values()) - PLANTED["missing_in_ledger"])
    cursor = 0

    def take(count: int) -> list[int]:
        nonlocal cursor
        picked = targets[cursor : cursor + count]
        cursor += count
        return picked

    adjustments = []
    for k, i in enumerate(take(PLANTED["amount_mismatch"])):
        shortfall = rng.choice([110, 220, 330])  # e.g. a fee deducted by the bank
        bank[i]["amount"] -= shortfall
        if k < RESOLVED_BY_ADJUSTMENT:
            stakeholder, note = SAMPLE_INVESTIGATIONS[k % len(SAMPLE_INVESTIGATIONS)]
            adjustments.append(
                {"reference": ledger[i]["reference"], "amount": shortfall, "stakeholder": stakeholder, "note": note}
            )

    for i in take(PLANTED["date_mismatch"]):
        bank[i]["date"] = ledger[i]["date"] + timedelta(days=rng.randint(1, 5))

    for i in take(PLANTED["suggested_match"]):
        bank[i]["reference"] = ""  # the bank lost the reference

    for i in take(PLANTED["duplicate"]):
        bank.append(dict(bank[i]))  # the same payment was posted twice

    missing_in_bank = set(take(PLANTED["missing_in_bank"]))
    bank = [txn for i, txn in enumerate(bank) if i not in missing_in_bank]

    for j in range(PLANTED["missing_in_ledger"]):
        bank.append(
            {
                "reference": f"TXN9{j:05d}",
                "date": start + timedelta(days=rng.randint(0, 27)),
                "amount": rng.randint(50, 5000) * 10,
                "merchant": rng.choice(MERCHANTS),
            }
        )

    ledger_df = pd.DataFrame(ledger).sort_values("date").reset_index(drop=True)
    bank_df = pd.DataFrame(bank).sort_values("date").reset_index(drop=True)
    adjustments_df = pd.DataFrame(adjustments, columns=["reference", "amount", "stakeholder", "note"])
    return ledger_df, bank_df, adjustments_df


if __name__ == "__main__":
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    ledger_df, bank_df, adjustments_df = generate()
    ledger_df.to_csv(data_dir / "ledger_sample.csv", index=False)
    bank_df.to_csv(data_dir / "bank_sample.csv", index=False)
    adjustments_df.to_csv(data_dir / "adjustments_sample.csv", index=False)
    print(
        f"Wrote {len(ledger_df)} ledger rows, {len(bank_df)} bank rows and "
        f"{len(adjustments_df)} adjustments to {data_dir}"
    )
    print("Planted problems:", PLANTED)
