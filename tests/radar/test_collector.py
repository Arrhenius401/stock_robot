"""配置雷达采集守护测试。"""

from radar.collector import RadarCollector


def test_collector_runs_each_pool_when_one_pool_fails():
    calls: list[str] = []
    recorded: list[tuple[str, str, str | None]] = []

    def refresh(universe_id: str) -> str:
        calls.append(universe_id)
        if universe_id == "cn_hk_etf":
            raise RuntimeError("上游暂不可用")
        return "run-overseas"

    collector = RadarCollector(
        refresh,
        ("cn_hk_etf", "overseas_etf"),
        lambda universe_id, status, result: recorded.append((universe_id, status, result)),
    )

    result = collector.run_once()

    assert calls == ["cn_hk_etf", "overseas_etf"]
    assert result["cn_hk_etf"].startswith("failed: 上游暂不可用")
    assert result["overseas_etf"] == "run-overseas"
    assert recorded == [
        ("cn_hk_etf", "failed", "上游暂不可用"),
        ("overseas_etf", "completed", "run-overseas"),
    ]


def test_collector_registers_weekday_shanghai_schedule(mocker):
    scheduler = mocker.Mock()
    mocker.patch("radar.collector.BlockingScheduler", return_value=scheduler)
    collector = RadarCollector(lambda universe_id: universe_id, ("cn_hk_etf",))

    collector.serve(hour=18, minute=30)

    scheduler.add_job.assert_called_once()
    _, kwargs = scheduler.add_job.call_args
    assert kwargs["id"] == "radar-collector"
    assert kwargs["max_instances"] == 1
    scheduler.start.assert_called_once()
