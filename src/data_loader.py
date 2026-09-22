"""
Loads the Olist CSV files (or the generated DEMO dataset).

Rules:
  * If data/raw/ contains the required Olist files -> use them  (source = "OLIST")
  * Otherwise fall back to data/demo/                            -> (source = "DEMO")
  * Missing optional files never crash the pipeline: the loader simply
    returns None for them and the rest of the code adapts.
"""

import pandas as pd

import config


# Columns that must be parsed as datetimes, per table.
DATE_COLUMNS = {
    "orders": [
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ],
    "order_items": ["shipping_limit_date"],
    "reviews": ["review_creation_date", "review_answer_timestamp"],
}


def _folder_has_required(folder):
    """True if the folder contains every file listed in REQUIRED_TABLES."""
    for key in config.REQUIRED_TABLES:
        if not (folder / config.OLIST_FILES[key]).exists():
            return False
    return True


def detect_data_source():
    """
    Decide which folder to read from.

    Returns (source_name, folder) where source_name is "OLIST" or "DEMO".
    Raises FileNotFoundError only if neither folder is usable.
    """
    if _folder_has_required(config.RAW_DIR):
        return "OLIST", config.RAW_DIR
    if _folder_has_required(config.DEMO_DIR):
        return "DEMO", config.DEMO_DIR
    raise FileNotFoundError(
        "No dataset found.\n"
        f"  Looked for {config.OLIST_FILES['orders']} and "
        f"{config.OLIST_FILES['order_items']} in:\n"
        f"    {config.RAW_DIR}\n"
        f"    {config.DEMO_DIR}\n"
        "Fix: run  python scripts/generate_demo_data.py  to create demo data,\n"
        "or copy the Olist CSV files into data/raw/."
    )


def _read_csv(path, date_cols=None):
    """Read one CSV defensively: never explode on encoding or bad dates."""
    try:
        df = pd.read_csv(path, low_memory=False)
    except UnicodeDecodeError:
        # Some Olist mirrors ship latin-1 encoded review text.
        df = pd.read_csv(path, low_memory=False, encoding="latin-1")

    for col in date_cols or []:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def load_raw_tables(verbose=True):
    """
    Load every available table.

    Returns (tables_dict, source_name). Tables that do not exist are simply
    absent from the dictionary - callers must use .get().
    """
    source, folder = detect_data_source()
    tables = {}

    for key, filename in config.OLIST_FILES.items():
        path = folder / filename
        if not path.exists():
            if key in config.REQUIRED_TABLES:
                raise FileNotFoundError(f"Required file missing: {path}")
            if verbose:
                print(f"  [skip]  {filename} (optional, not found)")
            continue

        df = _read_csv(path, DATE_COLUMNS.get(key))
        if df.empty:
            if verbose:
                print(f"  [empty] {filename} - ignored")
            continue

        tables[key] = df
        if verbose:
            print(f"  [ok]    {filename:<45} {len(df):>7,} rows")

    if verbose:
        print(f"  data source: {source}  ({folder})")
    return tables, source


def check_columns(df, required, table_name):
    """
    Verify that a dataframe has the columns we rely on.
    Returns a list of missing column names (empty list = fine).
    """
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"  [warn] table '{table_name}' is missing columns: {missing}")
    return missing
