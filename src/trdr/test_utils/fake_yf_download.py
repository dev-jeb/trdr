import datetime
import pandas as pd


def fake_yf_download(*args, **kwargs):
    """
    This returns fake batch stock data for two symbols. This is what yahoo finance returns for a batch download request
    grouped by symbol over a 3 day period.
    """
    n = 5
    dates = pd.bdate_range(end=datetime.date(2026, 3, 27), periods=n)

    data = {
        ("AAPL", "Open"): list(range(100, 100 + n)),
        ("AAPL", "High"): list(range(110, 110 + n)),
        ("AAPL", "Low"): list(range(90, 90 + n)),
        ("AAPL", "Close"): list(range(105, 105 + n)),
        ("AAPL", "Volume"): list(range(1000, 1000 + n * 100, 100)),
        ("MSFT", "Open"): list(range(200, 200 + n)),
        ("MSFT", "High"): list(range(210, 210 + n)),
        ("MSFT", "Low"): list(range(190, 190 + n)),
        ("MSFT", "Close"): list(range(205, 205 + n)),
        ("MSFT", "Volume"): list(range(2000, 2000 + n * 100, 100)),
        # this is what is returned when a symbol is not found
        ("ABCDEFG", "Open"): [None] * n,
        ("ABCDEFG", "High"): [None] * n,
        ("ABCDEFG", "Low"): [None] * n,
        ("ABCDEFG", "Close"): [None] * n,
        ("ABCDEFG", "Volume"): [None] * n,
        # this is what is returned when we hit the rate limit
        ("AMZN", "Open"): None,
        ("AMZN", "High"): None,
        ("AMZN", "Low"): None,
        ("AMZN", "Close"): None,
        ("AMZN", "Volume"): None,
    }

    return pd.DataFrame(data, index=dates)
