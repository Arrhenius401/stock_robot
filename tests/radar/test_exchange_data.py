"""交易所公开日终 CSV 兜底的数据边界测试。"""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from radar.data import OfficialExchangeETFDataProvider, RadarDataError


def _config(path: Path, template: str, *, enabled: bool = True) -> None:
    path.write_text(
        "sources:\n"
        f"  sse:\n    enabled: {str(enabled).lower()}\n    url_template: '{template}'\n    encoding: utf-8\n"
        f"  szse:\n    enabled: {str(enabled).lower()}\n    url_template: '{template}'\n    encoding: utf-8\n",
        encoding="utf-8",
    )


def test_official_exchange_downloads_configured_official_csv():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        config = Path(tmp_dir) / "sources.yaml"
        _config(config, "https://query.sse.com.cn/etf/{symbol}?start={start}&end={end}")
        requested: list[str] = []
        provider = OfficialExchangeETFDataProvider(
            config_path=config,
            downloader=lambda url: requested.append(url) or "日期,开盘,最高,最低,收盘,成交额\n2024-01-02,1,2,0.5,1.5,100\n".encode(),
        )

        result = provider.fetch_daily("510300", date(2024, 1, 1), date(2024, 1, 3))

        assert len(result) == 1
        assert result.iloc[0].close == 1.5
        assert requested == ["https://query.sse.com.cn/etf/510300?start=20240101&end=20240103"]


def test_official_exchange_rejects_non_official_download_host():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        config = Path(tmp_dir) / "sources.yaml"
        _config(config, "https://example.com/etf/{symbol}")
        provider = OfficialExchangeETFDataProvider(config_path=config)

        with pytest.raises(RadarDataError, match="必须为上交所或深交所"):
            provider.fetch_daily("510300", date(2024, 1, 1), date(2024, 1, 3))


def test_official_exchange_skips_disabled_source_without_network_request():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        config = Path(tmp_dir) / "sources.yaml"
        _config(config, "https://query.sse.com.cn/etf/{symbol}", enabled=False)
        provider = OfficialExchangeETFDataProvider(config_path=config)

        with pytest.raises(RadarDataError, match="尚未启用"):
            provider.fetch_daily("510300", date(2024, 1, 1), date(2024, 1, 3))
