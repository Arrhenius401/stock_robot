"""检查数据取数入口：遗留清单、动态调用和静态绕行。"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from core.collection_contract import FetchSite, find_violations

REPOSITORY = Path(__file__).resolve().parents[1]


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def test_declared_literal_fetch_is_allowed(tmp_path):
    write(tmp_path, "src/core/legacy.py", """
        def run(source):
            source.fetch("000001", data_type="price")
    """)
    sites = (FetchSite("src/core/legacy.py", "run", ("price",), "测试", "test"),)
    assert find_violations(tmp_path, sites) == []


def test_declared_dynamic_fetch_is_allowed(tmp_path):
    write(tmp_path, "src/core/legacy.py", """
        def run(source, data_type):
            source.fetch("000001", **{"data_type": data_type})
    """)
    sites = (FetchSite("src/core/legacy.py", "run", (None,), "测试", "test"),)
    assert find_violations(tmp_path, sites) == []


def test_new_unlisted_fetch_is_rejected(tmp_path):
    write(tmp_path, "src/core/new.py", """
        def run(source):
            source.fetch("000001", data_type="financial")
    """)
    violations = find_violations(tmp_path, ())
    assert violations
    assert "financial" in violations[0]


def test_fetch_result_outside_collector_is_rejected(tmp_path):
    write(tmp_path, "src/core/new.py", """
        def run(source):
            source.fetch_result("000001")
    """)
    violations = find_violations(tmp_path, ())
    assert any("fetch_result" in violation for violation in violations)


def test_gold_provider_must_use_adapter_boundary(tmp_path):
    write(tmp_path, "src/gold/bypass.py", """
        def collect(source):
            source.request_executor("gold.sources", "fetch_source", {})
    """)
    assert any("黄金来源" in message for message in find_violations(tmp_path, ()))


def test_gold_direct_fetch_source_is_rejected_even_with_alias(tmp_path):
    write(tmp_path, "src/gold/bypass.py", """
        from gold.sources import fetch_source as direct
        def collect():
            direct(key="domestic")
    """)
    assert any("黄金来源" in message for message in find_violations(tmp_path, ()))


def test_gold_direct_fetch_source_outside_gold_is_rejected(tmp_path):
    write(tmp_path, "src/data/bypass.py", """
        from gold.sources import fetch_source as direct
        def collect():
            direct(key="domestic")
    """)
    assert any("黄金来源" in message for message in find_violations(tmp_path, ()))


def test_other_provider_boundaries_are_not_gold_violations(tmp_path):
    write(tmp_path, "src/radar/data.py", """
        from radar.provider_request import bounded_provider_call
        def collect():
            return bounded_provider_call("akshare", "fund_etf_hist_em")
    """)
    assert find_violations(tmp_path, ()) == []


def test_gold_collector_contract_entry_is_allowed(tmp_path):
    write(tmp_path, "src/gold/data.py", """
        class GoldCollector:
            def _request(self, source):
                return source.fetch_result("GOLD")
    """)
    assert find_violations(tmp_path, ()) == []


def test_stale_manifest_entry_is_rejected(tmp_path):
    write(tmp_path, "src/core/legacy.py", """
        def run():
            return None
    """)
    sites = (FetchSite("src/core/legacy.py", "missing", ("price",), "测试", "test"),)
    violations = find_violations(tmp_path, sites)
    assert any("已不存在" in violation for violation in violations)


def test_repository_entrypoints_are_clean():
    assert find_violations(REPOSITORY) == []


def test_check_script_runs_from_repository():
    env = dict(
        os.environ,
        PYTHONPATH=f"{REPOSITORY / 'src'}{os.pathsep}{REPOSITORY}",
        PYTHONIOENCODING="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(REPOSITORY / "scripts/check-data-entrypoints.py")],
        cwd=REPOSITORY, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    assert result.returncode == 0, result.stderr


def test_pipeline_fetch_result_calls_are_declared(tmp_path):
    write(tmp_path, "src/core/pipeline.py", """
        class Pipeline:
            def collect(self, source):
                def fetch_one():
                    source.fetch_result("000001")
                return fetch_one()
            def _collect_hk_news(self, source):
                return source.fetch_result("00700.HK")
    """)
    assert find_violations(tmp_path, ()) == []
