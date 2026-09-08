"""雷达日线评分测试。"""

from typing import Any, cast

import pandas as pd

from radar.scoring import score_etfs


def _history(multiplier: float, amount: float = 100.0) -> pd.DataFrame:
    closes = [100 * (1 + multiplier * index / 60) for index in range(61)]
    return pd.DataFrame({"close": closes, "amount": [amount] * 61})


def test_scores_are_category_local_and_ranked():
    histories = {"a": _history(0.1), "b": _history(0.2), "c": _history(0.3)}
    result = score_etfs(histories, {"a": "cn", "b": "cn", "c": "cn"}).set_index("symbol")

    assert result.loc["c", "rank"] == 1
    assert result.loc["c", "score"] > result.loc["a", "score"]
    grade: str = str(cast(Any, result.loc["a", "grade"]))
    assert grade in {"偏好", "观察", "谨慎"}


def test_insufficient_history_or_category_is_unavailable():
    histories = {"a": _history(0.1).iloc[:20], "b": _history(0.2), "c": _history(0.3)}
    result = score_etfs(histories, {"a": "cn", "b": "hk", "c": "hk"})

    assert bool(cast(Any, result["score"]).isna().all())
    grades = {str(value) for value in cast(Any, result["grade"]).tolist()}
    assert grades == {"unavailable"}
