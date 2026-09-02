"""Export small aggregate CSVs that feed the public Looker Studio dashboard.

Two constraints shape this script.

Looker Studio cannot compute a median, so every median is pre-computed here at the
exact grain of the chart that shows it. A median of medians is not a median, so the
grain has to match the chart, not merely be finer than it.

The files land in `reports/agg/` and not in `data/processed/`, because the latter is
git-ignored. The dashboard reads them from the repository, so a file git does not
track is a file the dashboard cannot reach.

One convention throughout: every share is a fraction between 0 and 1, named `*_share`,
so the Percent field type formats it correctly. Mixing fractions with percentage points
in one export is how a dashboard ends up reporting a saving of 2156%.
"""

import pandas as pd
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PROCESSED_CSV_PATH = PROJECT_DIR / "data" / "processed" / "energia_pl.csv"
AGG_DIR = PROJECT_DIR / "reports" / "agg"

MAIN_FRACTION = 0.20
CURVE_FRACTIONS = [i / 100 for i in range(5, 50, 5)]

# Regression guard: the saving reported by notebooks/03_rekomendacja.ipynb on this
# exact data window. Checked only when the window still matches, so a data refresh
# does not turn a legitimately new number into a failed run.
NOTEBOOK_DATE_MAX = "2026-08-19"
NOTEBOOK_SAVING_20 = 98.70

# RES share buckets, 10 percentage points each. The observed maximum is 0.87, so the
# top bucket stays empty and is dropped by `observed=True` rather than exported as NaN.
RES_BIN_EDGES = [i / 10 for i in range(11)]
RES_BIN_LABELS = [f"{int(low * 100)}-{int(low * 100) + 10}%" for low in RES_BIN_EDGES[:-1]]
RES_BIN_LOWS = dict(zip(RES_BIN_LABELS, RES_BIN_EDGES[:-1]))

PART_OF_DAY_EDGES = [0, 6, 10, 16, 20, 24]
PART_OF_DAY_LABELS = ["night", "morning", "midday", "afternoon", "evening"]

# Below this many quarters a cell's median is noise rather than a result. Exported as a
# flag instead of being filtered out here: a missing cell would read as "no effect",
# while a cell marked unreliable reads as "not enough data" - which is what it means.
MIN_RELIABLE_QUARTERS = 30


def add_day_position(df):
    """Add the position of a quarter in the price ranking of its own day, scaled to 0-1.

    Days of the clock change hold 92 and 100 quarters instead of 96, so a basket of a
    fixed size would mean 24% of the daily volume on one day and 25% on another. The
    normalised position keeps `day_position <= 0.2` meaning the cheapest fifth of the
    day everywhere in the year.
    """
    rank = df.groupby("business_date")["rce_pln"].rank(method="first")
    day_size = df.groupby("business_date")["rce_pln"].transform("size")
    df["day_position"] = (rank - 0.5) / day_size
    return df


def daily_shift(data, fraction):
    """Daily cost of a flat consumption profile before and after moving `fraction`
    of the daily volume from the most expensive to the cheapest quarters of the same day.

    Mirrors the model in notebooks/03_rekomendacja.ipynb. The duplication is deliberate
    - the script has to stand alone for the scheduled refresh - and `check_exports`
    compares the result against the notebook so the two cannot drift apart unnoticed.
    """
    if not 0 < fraction < 0.5:
        raise ValueError("fraction must be between 0 and 0.5")

    baseline = data.groupby("business_date")["rce_pln"].mean()
    cheap = data[data["day_position"] <= fraction].groupby("business_date")["rce_pln"].mean()
    expensive = data[data["day_position"] > 1 - fraction].groupby("business_date")["rce_pln"].mean()

    result = pd.DataFrame({"baseline": baseline, "cheap": cheap, "expensive": expensive})
    result["spread"] = result["expensive"] - result["cheap"]
    result["shifted"] = result["baseline"] - fraction * result["spread"]
    result["saving"] = result["baseline"] - result["shifted"]
    return result


def kpi_row(df, daily):
    """One row of global figures for the dashboard scorecards.

    Exported ready-made because a scorecard fed from the daily file would average
    daily medians, and the average of daily medians is not the median of the data.
    """
    row = {
        "quarters": len(df),
        "days": df["business_date"].nunique(),
        "date_min": df["business_date"].min(),
        "date_max": df["business_date"].max(),
        "price_mean": df["rce_pln"].mean(),
        "price_median": df["rce_pln"].median(),
        "price_std": df["rce_pln"].std(),
        "price_min": df["rce_pln"].min(),
        "price_max": df["rce_pln"].max(),
        "negative_quarters": int(df["is_negative"].sum()),
        "negative_share": df["is_negative"].mean(),
        "saving_20_pln": daily["saving"].mean(),
        "saving_20_share": daily["saving"].mean() / daily["baseline"].mean(),
    }
    return pd.DataFrame([row]).round({
        "price_mean": 2, "price_median": 2, "price_std": 2, "price_min": 2, "price_max": 2,
        "negative_share": 4, "saving_20_pln": 2, "saving_20_share": 4,
    })


def daily_aggregate(df, daily):
    """One row per day: price statistics, RES share and the spread behind the saving."""
    agg = df.groupby("business_date").agg(
        quarters=("rce_pln", "size"),
        price_mean=("rce_pln", "mean"),
        price_median=("rce_pln", "median"),
        price_min=("rce_pln", "min"),
        price_max=("rce_pln", "max"),
        price_std=("rce_pln", "std"),
        res_share_mean=("res_share", "mean"),
        negative_quarters=("is_negative", "sum"),
        month=("month", "first"),
        day_name=("day_name", "first"),
        dow_num=("day_of_week", "first"),
        is_weekend=("is_weekend", "first"),
    )
    agg["year"] = pd.to_datetime(agg.index).year
    agg["spread_20"] = daily["spread"]

    columns = ["year", "month", "day_name", "dow_num", "is_weekend", "quarters",
               "price_mean", "price_median", "price_min", "price_max", "price_std",
               "res_share_mean", "negative_quarters", "spread_20"]
    return agg[columns].round({
        "price_mean": 2, "price_median": 2, "price_min": 2, "price_max": 2,
        "price_std": 2, "res_share_mean": 4, "spread_20": 2,
    }).reset_index()


def hour_dow_aggregate(df):
    """One row per hour and weekday, 168 cells of the heatmap.

    `dow_num` travels with the day name because Looker Studio sorts text fields
    alphabetically, which would order the week Friday, Monday, Saturday.
    """
    agg = df.groupby(["day_name", "day_of_week", "hour"]).agg(
        price_median=("rce_pln", "median"),
        price_mean=("rce_pln", "mean"),
        quarters=("rce_pln", "size"),
    ).reset_index()

    agg = agg.rename(columns={"day_of_week": "dow_num"})
    return agg[["day_name", "dow_num", "hour", "price_median", "price_mean", "quarters"]].round({
        "price_median": 2, "price_mean": 2,
    })


def res_bins_aggregate(df):
    """Price by RES share bucket, split by part of the day.

    The split is the control from notebook 02: the RES share is tied to the time of
    day, so a plain price-against-share chart would show the daily profile as much as
    the effect of wind and solar. `quarters` is exported alongside because some cells
    are nearly empty - above 70% the night never happens - and a median over ten
    observations is noise that the dashboard has to be able to filter out.

    `negative_quarters` is a count and not only a share, so the dashboard can add the
    parts of the day back together as SUM(negative) / SUM(quarters). Averaging the
    shares instead would weight a cell of ten observations like one of ten thousand.
    """
    binned = df.assign(
        res_bin=pd.cut(df["res_share"], bins=RES_BIN_EDGES, labels=RES_BIN_LABELS, right=False),
        part_of_day=pd.cut(df["hour"], bins=PART_OF_DAY_EDGES, labels=PART_OF_DAY_LABELS,
                           right=False),
    )

    agg = binned.groupby(["res_bin", "part_of_day"], observed=True).agg(
        quarters=("rce_pln", "size"),
        price_mean=("rce_pln", "mean"),
        price_median=("rce_pln", "median"),
        negative_quarters=("is_negative", "sum"),
        negative_share=("is_negative", "mean"),
    ).reset_index()

    agg = agg.rename(columns={"res_bin": "res_bin_label"})
    agg["res_bin_label"] = agg["res_bin_label"].astype(str)
    agg["res_bin_low"] = agg["res_bin_label"].map(RES_BIN_LOWS)
    agg["part_of_day"] = agg["part_of_day"].astype(str)
    agg["is_reliable"] = agg["quarters"] >= MIN_RELIABLE_QUARTERS

    columns = ["res_bin_label", "res_bin_low", "part_of_day", "quarters", "is_reliable",
               "price_mean", "price_median", "negative_quarters", "negative_share"]
    return agg[columns].round({"price_mean": 2, "price_median": 2, "negative_share": 4})


def saving_curve(df):
    """One row per size of the shift, from 5% to 45% of the daily volume."""
    rows = []
    for fraction in CURVE_FRACTIONS:
        daily = daily_shift(df, fraction)
        rows.append({
            "shift_pct": int(round(fraction * 100)),
            "baseline": daily["baseline"].mean(),
            "cheap": daily["cheap"].mean(),
            "expensive": daily["expensive"].mean(),
            "spread": daily["spread"].mean(),
            "saving_pln": daily["saving"].mean(),
            "saving_share": daily["saving"].mean() / daily["baseline"].mean(),
        })
    return pd.DataFrame(rows).round({
        "baseline": 2, "cheap": 2, "expensive": 2, "spread": 2,
        "saving_pln": 2, "saving_share": 4,
    })


def check_exports(df, daily, frames):
    """Stop the run instead of publishing a dashboard built on wrong numbers."""
    quarters = frames["agg_daily"]["quarters"].sum()
    if quarters != len(df):
        raise ValueError(f"daily rows cover {quarters} quarters, source has {len(df)}")

    cells = len(frames["agg_hour_dow"])
    if cells != 24 * 7:
        raise ValueError(f"hour x weekday grid has {cells} cells, expected {24 * 7}")

    saving = daily["saving"].mean()
    if df["business_date"].max() == NOTEBOOK_DATE_MAX:
        if abs(saving - NOTEBOOK_SAVING_20) > 0.01:
            raise ValueError(f"saving at 20% is {saving:.2f}, notebook 03 reports "
                             f"{NOTEBOOK_SAVING_20} on the same window - the model drifted")
        print(f"saving at 20% matches notebook 03: {saving:.2f} PLN/MWh")
    else:
        print(f"data window ends {df['business_date'].max()}, not {NOTEBOOK_DATE_MAX} - "
              f"notebook comparison skipped, saving at 20% is {saving:.2f} PLN/MWh")


def main():
    df = pd.read_csv(PROCESSED_CSV_PATH)
    df["is_negative"] = df["rce_pln"] < 0
    df = add_day_position(df)
    print(f"loaded: {len(df)} quarters, {df['business_date'].nunique()} days")

    daily = daily_shift(df, MAIN_FRACTION)

    frames = {
        "agg_kpi": kpi_row(df, daily),
        "agg_daily": daily_aggregate(df, daily),
        "agg_hour_dow": hour_dow_aggregate(df),
        "agg_res_bins": res_bins_aggregate(df),
        "agg_saving_curve": saving_curve(df),
    }

    check_exports(df, daily, frames)

    AGG_DIR.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        path = AGG_DIR / f"{name}.csv"
        frame.to_csv(path, index=False)
        print(f"{path.relative_to(PROJECT_DIR)}: {len(frame)} rows, {len(frame.columns)} columns")


if __name__ == "__main__":
    main()
