"""Payment Reconciliation Checker - Streamlit web app.

Run locally:  streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from reconcile import (
    ADJUSTMENT_COLUMNS,
    AMOUNT_MISMATCH,
    BREAK_STATUSES,
    MATCHED_WITH_ADJUSTMENT,
    REQUIRED_COLUMNS,
    build_excel_report,
    match_rate,
    prepare_adjustments,
    reconcile,
    summarize,
    unused_adjustments,
)

DATA_DIR = Path(__file__).parent / "data"
DATE_COLUMNS = ["date_ledger", "date_bank"]
NUMBER_COLUMNS = ["amount_ledger", "amount_bank", "adjustment", "amount_difference", "days_difference"]

st.set_page_config(page_title="Payment Reconciliation Checker", layout="wide")


@st.cache_data
def load_sample_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ledger = pd.read_csv(DATA_DIR / "ledger_sample.csv")
    bank = pd.read_csv(DATA_DIR / "bank_sample.csv")
    adjustments = pd.read_csv(DATA_DIR / "adjustments_sample.csv")
    return ledger, bank, adjustments


def empty_adjustments() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "reference": pd.Series(dtype="object"),
            "amount": pd.Series(dtype="float"),
            "stakeholder": pd.Series(dtype="object"),
            "note": pd.Series(dtype="object"),
        }
    )


def for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Format dates as text and amounts as whole numbers for the tables."""
    df = df.copy()
    for col in DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d")
    for col in NUMBER_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col]).round().astype("Int64")
    return df


# ---------------------------------------------------------------------------
# Sidebar: choose data and matching rules
# ---------------------------------------------------------------------------
st.sidebar.header("1. Data")
source = st.sidebar.radio("Data source", ["Sample data", "Upload my own CSV files"])

st.sidebar.header("2. Matching rules")
st.sidebar.markdown(
    "- Amounts must match **exactly**\n"
    "- Dates must match **exactly**\n\n"
    "Any difference stays open as a break until it is investigated. "
    "Amount breaks are closed by recording an adjustment."
)

# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------
st.title("Payment Reconciliation Checker")
st.caption(
    "Compares an internal payment ledger with a bank statement, flags every break, "
    "and lets you record the adjustments that resolve them. All sample data is synthetic."
)

if source == "Sample data":
    ledger_raw, bank_raw, starting_adjustments = load_sample_data()
else:
    st.sidebar.caption(f"Required columns: {', '.join(REQUIRED_COLUMNS)} (merchant is optional).")
    ledger_file = st.sidebar.file_uploader("Internal ledger (CSV)", type="csv")
    bank_file = st.sidebar.file_uploader("Bank statement (CSV)", type="csv")
    adjustments_file = st.sidebar.file_uploader("Adjustments (CSV, optional)", type="csv")
    if ledger_file is None or bank_file is None:
        st.info(
            "Upload both CSV files in the sidebar to start. Each file needs the columns "
            f"**{', '.join(REQUIRED_COLUMNS)}**, and can also have **merchant**."
        )
        st.stop()
    ledger_raw = pd.read_csv(ledger_file)
    bank_raw = pd.read_csv(bank_file)
    starting_adjustments = empty_adjustments()
    if adjustments_file is not None:
        try:
            starting_adjustments = prepare_adjustments(pd.read_csv(adjustments_file))
        except ValueError as error:
            st.error(str(error))
            st.stop()

# The overview is filled in after the adjustments editor has been read.
overview = st.container()
breaks_tab, adjustments_tab, all_tab, input_tab = st.tabs(
    ["Open breaks", "Adjustments", "All results", "Input data"]
)

with adjustments_tab:
    st.markdown(
        "After investigating a break with the stakeholder, record the missing amount here. "
        "The adjustment is added to the bank side for that reference. The break is only "
        f"matched when the difference becomes exactly zero (status **{MATCHED_WITH_ADJUSTMENT}**)."
    )
    adjustments = st.data_editor(
        starting_adjustments,
        num_rows="dynamic",
        hide_index=True,
        column_order=ADJUSTMENT_COLUMNS,
        column_config={
            "reference": st.column_config.TextColumn("Reference", help="Reference of the break"),
            "amount": st.column_config.NumberColumn("Amount (JPY)", help="Amount to add to the bank side", step=1),
            "stakeholder": st.column_config.TextColumn("Stakeholder", help="Who confirmed the reason"),
            "note": st.column_config.TextColumn("Investigation note"),
        },
        key=f"adjustments_editor_{source}",
    )

try:
    result = reconcile(ledger_raw, bank_raw, adjustments=adjustments)
except ValueError as error:
    overview.error(str(error))
    st.stop()

breaks = result[result["status"].isin(BREAK_STATUSES)]
summary = summarize(result)
break_value = breaks["amount_ledger"].fillna(breaks["amount_bank"]).abs().sum()
adjusted_count = int((result["status"] == MATCHED_WITH_ADJUSTMENT).sum())

with overview:
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Ledger transactions", f"{len(ledger_raw):,}")
    col2.metric("Bank transactions", f"{len(bank_raw):,}")
    col3.metric("Match rate", f"{match_rate(result):.1%}", help="Matched, with or without an adjustment")
    col4.metric("Open breaks", f"{len(breaks):,}", help=f"{adjusted_count} break(s) resolved with an adjustment")
    col5.metric("Value of open breaks (JPY)", f"{break_value:,.0f}")

    st.subheader("Results by status")
    chart_col, table_col = st.columns([3, 2])
    chart_col.bar_chart(summary.set_index("status")["count"])
    table_col.dataframe(
        summary.assign(total_amount=summary["total_amount"].round().astype("Int64")),
        hide_index=True,
    )

with breaks_tab:
    st.markdown(
        f"Breaks stay open until someone resolves them. For an **{AMOUNT_MISMATCH}**, the note shows "
        "the exact amount needed. Confirm the reason with the stakeholder, then record it in the "
        "**Adjustments** tab. A **Date mismatch** must be checked with the stakeholder, because tax "
        "and exchange rates depend on the date."
    )
    statuses_found = [status for status in BREAK_STATUSES if status in set(breaks["status"])]
    chosen = st.multiselect("Show statuses", statuses_found, default=statuses_found)
    filtered = breaks[breaks["status"].isin(chosen)]
    st.dataframe(for_display(filtered), hide_index=True)

    download_col1, download_col2 = st.columns(2)
    download_col1.download_button(
        "Download open breaks (CSV)",
        data=filtered.to_csv(index=False).encode("utf-8"),
        file_name="open_breaks.csv",
        mime="text/csv",
    )
    download_col2.download_button(
        "Download full report (Excel)",
        data=build_excel_report(result, adjustments),
        file_name="reconciliation_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

with adjustments_tab:
    unused = unused_adjustments(adjustments, result)
    if not unused.empty:
        st.warning(
            "These adjustments do not belong to any matched pair of transactions, so they were not "
            f"applied: {', '.join(unused['reference'])}"
        )

with all_tab:
    st.dataframe(for_display(result), hide_index=True)

with input_tab:
    left, right = st.columns(2)
    left.markdown("**Internal ledger**")
    left.dataframe(ledger_raw, hide_index=True)
    right.markdown("**Bank statement**")
    right.dataframe(bank_raw, hide_index=True)

with st.expander("How matching works"):
    st.markdown(
        """
1. **Duplicate** - a reference appears more than once in the same file. The first copy is kept for matching.
2. **Reference match** - the same reference exists on both sides. Recorded adjustments are added to the bank amount, then:
   - amounts are not exactly equal -> **Amount mismatch** (stays open)
   - dates are not the same -> **Date mismatch** (stays open)
   - an adjustment closed the difference -> **Matched with adjustment**
   - otherwise -> **Matched**
3. **Suggested match** - no shared reference, but amount, date and merchant are the same. A person must confirm it.
4. **Missing in bank / Missing in ledger** - the transaction exists on one side only.
"""
    )
