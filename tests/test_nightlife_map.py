from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from nightlife_map import filter_map_rows, hour_order


def test_hour_order_crosses_midnight():
    assert [hour_order(h) for h in [18, 23, 0, 5]] == [0, 5, 6, 11]


def test_ratio_excludes_low_volume_and_airports():
    rows = pd.DataFrame([
        {"zone": "West Village", "enough_trips": True,
         "excluded_reason": None, "nightlife_score": 2.4},
        {"zone": "Tiny zone", "enough_trips": False,
         "excluded_reason": "low volume", "nightlife_score": 9.0},
        {"zone": "Airport", "enough_trips": True,
         "excluded_reason": "airport", "nightlife_score": 5.0},
    ])
    good = filter_map_rows(rows, "nightlife_score")
    assert good["zone"].tolist() == ["West Village"]
