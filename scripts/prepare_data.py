"""Clean the raw PSE data and merge prices with generation into one dataset."""

import pandas as pd
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PRICES_CSV_PATH = PROJECT_DIR / "data" / "raw" / "rce_pln.csv"
GEN_CSV_PATH = PROJECT_DIR / "data" / "raw" / "his_wlk_cal.csv"
PROCESSED_CSV_PATH = PROJECT_DIR / "data" / "processed" / "energia_pl.csv"

# Only these 5 of the 24 his-wlk-cal columns. The rest stays untouched in data/raw/.
GEN_COLUMNS = ["business_date", "period", "pv", "wi", "demand"]


def merge_sources(prices, generation):
    """Join prices with generation on business date and quarter-hour period."""
    return prices.merge(generation,
                        on=["business_date", "period"],
                        validate="one_to_one",
                        how="inner")


def add_hour(df):
    """Add the hour taken from the START of the quarter-hour period.

    Not from `dtime`, which marks the END of the period. Using it would shift the
    daily profile by 15 minutes on 25% of the rows, without raising any error.
    """
    df["hour"] = df["period"].str.split(" - ").str[0].str[:2].astype(int)
    return df


def add_calendar_columns(df):
    """Add day of week, month, weekend flag and day name."""
    dt = pd.to_datetime(df["business_date"])
    df["day_of_week"] = dt.dt.day_of_week
    df["month"] = dt.dt.month
    df["is_weekend"] = dt.dt.day_of_week >= 5
    df["day_name"] = dt.dt.day_name()
    return df


def add_res_share(df):
    """Add the share of demand covered by wind and solar generation."""
    df["res_share"] = (df["pv"] + df["wi"]) / df["demand"]
    return df


def main():
    prices = pd.read_csv(PRICES_CSV_PATH)
    generation = pd.read_csv(GEN_CSV_PATH, usecols=GEN_COLUMNS)
    print(f"loaded: prices {len(prices)}, generation {len(generation)}")

    df = merge_sources(prices, generation)
    df = add_hour(df)
    df = add_calendar_columns(df)
    df = add_res_share(df)

    df.to_csv(PROCESSED_CSV_PATH, index=False)
    print(f"rows written: {len(df)}")
    print(f"date range: {df['business_date'].min()} - {df['business_date'].max()}")
    print(f"columns ({len(df.columns)}): {list(df.columns)}")


if __name__ == "__main__":
    main()
