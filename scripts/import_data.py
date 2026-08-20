"""Fetch PSE API data (prices, generation, demand) and store it in data/raw/."""

import pandas as pd
import requests
from pathlib import Path

BASE_URL = "https://api.raporty.pse.pl/api/"
PROJECT_DIR = Path(__file__).resolve().parent.parent
PRICES_CSV_PATH = PROJECT_DIR / "data" / "raw" / "rce_pln.csv"
GEN_CSV_PATH = PROJECT_DIR / "data" / "raw" / "his_wlk_cal.csv"
PAGE_SIZE = 5000
RECORDS_PER_DAY = 96


def fetch_range(endpoint, date_from, date_to):
    """Fetch records from one PSE endpoint for a date range, following pagination."""
    url = f"{BASE_URL}{endpoint}"
    params = {"$filter": f"business_date ge '{date_from}' and business_date le '{date_to}'",
              "$first": PAGE_SIZE}
    records = []

    while True:
        response = requests.get(url, params=params, timeout=90)
        response.raise_for_status()
        payload = response.json()
        records.extend(payload["value"])

        next_link = payload.get("nextLink")
        if not next_link:
            break
        url = next_link          # nextLink is a full, pre-encoded URL
        params = None            # so params must not be sent again

    return records


def check_completeness(df):
    """Report days that deviate from 96 records and duplicated quarter-hours."""
    counts = df.groupby("business_date").size()
    anomalies = counts[counts != RECORDS_PER_DAY]

    print(f"Days deviating from {RECORDS_PER_DAY} records: {len(anomalies)}")
    if len(anomalies):
        print(anomalies.to_string())
        print("(92 = DST spring forward, 100 = DST fall back — both correct)")

    duplicates = df.duplicated(subset=["business_date", "period"]).sum()
    print(f"Duplicated quarter-hours: {duplicates}")


def download(endpoint, csv_path, date_from, date_to):
    """Fetch one source month by month, save it to CSV and report completeness.

    Monthly chunks keep the number of requests low (~26 for two years) while a
    failed request costs one month, not the whole range. Returns the DataFrame
    so callers can use the data without reading the file back.
    """
    all_records = []

    for month_start in pd.date_range(date_from, date_to, freq="MS"):
        month_end = month_start + pd.offsets.MonthEnd(0)
        all_records.extend(fetch_range(endpoint, month_start.strftime("%Y-%m-%d"),
                                       month_end.strftime("%Y-%m-%d")))
        print(f"[{endpoint}] {month_start:%Y-%m} {len(all_records)}")

    df = pd.DataFrame(all_records)
    df.to_csv(csv_path, index=False)
    print(f"[{endpoint}] rows written: {len(df)}")
    print(f"[{endpoint}] rows read back: {len(pd.read_csv(csv_path))}")

    check_completeness(df)
    return df


if __name__ == "__main__":
    download("rce-pln", PRICES_CSV_PATH, "2024-06-01", "2026-08-20")
    download("his-wlk-cal", GEN_CSV_PATH, "2024-06-01", "2026-08-20")
