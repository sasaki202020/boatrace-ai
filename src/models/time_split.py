import pandas as pd

def time_series_split(X, y, dates, test_days=7):
    """
    日付順に並べ、直近 N 日間をテストデータにする。
    """
    if test_days < 1:
        raise ValueError("test_days must be at least 1")

    # Align by row position, not by external index labels.
    X = X.reset_index(drop=True)
    y = y.reset_index(drop=True)
    dates = pd.Series(pd.to_datetime(dates, errors="coerce")).reset_index(drop=True)
    unique_dates = sorted(dates.dropna().unique())
    if len(unique_dates) < 2:
        raise ValueError("At least two unique dates are required for time series split")

    effective_test_days = min(test_days, len(unique_dates) - 1)
    split_date = unique_dates[-effective_test_days]

    train_idx = dates < split_date
    test_idx = dates >= split_date

    if not train_idx.any() or not test_idx.any():
        raise ValueError("Time series split produced an empty train or test set")

    return X.loc[train_idx], X.loc[test_idx], y.loc[train_idx], y.loc[test_idx]
