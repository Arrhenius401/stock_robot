from data.schemas import AnalysisContext, AnalysisResult
from push.executor import PushExecutor
from push.models import Subscription, SubscriptionSymbol


class _Core:
    def __init__(self):
        self.pipeline = _Pipeline()
        self.index_pipeline = _IndexPipeline()


class _Pipeline:
    def run(self, symbol, name):
        ctx = AnalysisContext(symbol=symbol, name=name)
        return ([AnalysisResult(dimension="financial", status="ok", score=7.0,
                                summary="OK", metrics={})],
                {"bulk": "综合解读"}, ctx)


class _IndexPipeline:
    def run(self, targets):
        from datetime import date

        from data.schemas import IndexReport
        target = targets[0]
        report = IndexReport(
            code=target.symbol, name=target.name, date=date(2026, 8, 19),
            overview={}, section_technical={"趋势": "多头"}, section_valuation={},
            section_capital={}, section_macro=None, section_sentiment={},
            tag_technical="bull", tag_valuation="neutral", tag_capital="positive",
            tag_macro="na", tag_sentiment="neutral",
            composite_comment="", position_coeff=None, risk_list=[],
            visible_sections={"technical"},
        )
        return type("R", (), {"reports": [report], "compare": None, "errors": []})()


class _Backend:
    def __init__(self):
        self.calls = []

    def send(self, *, title, content, content_type):
        self.calls.append({"title": title, "content": content, "content_type": content_type})


class _Config:
    def get(self, key, default=None):
        if key == "push":
            return {"email": {"smtp_host": "h", "smtp_user": "u", "smtp_password": "p",
                              "to_addr": "a@b.c", "smtp_port": 465}}
        if key == "signal":
            return {"thresholds": {"attack": 7, "watch": 4},
                    "actions": {"attack": {"action": "建仓", "position": "60%"},
                                "watch": {"action": "观察", "position": "30%"},
                                "defend": {"action": "回避", "position": "0%"}}}
        return default


class TestPushExecutor:
    def test_stock_email_sends_full(self, mocker, tmp_path):
        from push.store import PushStore
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(name="t", symbols=[SubscriptionSymbol(symbol="600519")],
                                           channel="email", time="08:00"))
        backend = _Backend()
        mocker.patch("push.executor.get_backend", return_value=backend)
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        executor = PushExecutor(_Core(), store, _Config())
        sub = store.get(sub_id)
        assert sub is not None
        result = executor.run_subscription(sub)
        assert result["ok"] == 1
        assert backend.calls[0]["title"].startswith("[Stock Robot]")
        assert "综合解读" in backend.calls[0]["content"]  # 全文含 AI 解读
        run = store.last_run(sub_id)
        assert run is not None
        assert run["ok"] == 1

    def test_stock_wecom_sends_summary(self, mocker, tmp_path):
        from push.store import PushStore
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(name="t", symbols=[SubscriptionSymbol(symbol="600519")],
                                           channel="wecom", time="08:00"))
        backend = _Backend()
        mocker.patch("push.executor.get_backend", return_value=backend)
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        executor = PushExecutor(_Core(), store, _Config())
        sub = store.get(sub_id)
        assert sub is not None
        result = executor.run_subscription(sub)
        assert result["ok"] == 1
        assert "操作信号" in backend.calls[0]["content"]  # 摘要含信号
        assert "综合解读" not in backend.calls[0]["content"]

    def test_index_wecom_sends_summary(self, mocker, tmp_path):
        from push.store import PushStore
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(name="t", symbols=[SubscriptionSymbol(symbol="000300")],
                                           channel="wecom", time="08:00"))
        backend = _Backend()
        mocker.patch("push.executor.get_backend", return_value=backend)
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        executor = PushExecutor(_Core(), store, _Config())
        sub = store.get(sub_id)
        assert sub is not None
        result = executor.run_subscription(sub)
        assert result["ok"] == 1
        assert "沪深300" in backend.calls[0]["content"]
        assert "技术：bull" in backend.calls[0]["content"]

    def test_failed_symbol_isolated(self, mocker, tmp_path):
        from push.store import PushStore
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(name="t", symbols=[SubscriptionSymbol(symbol="600519"), SubscriptionSymbol(symbol="BAD!!")],
                                           channel="email", time="08:00"))
        backend = _Backend()
        mocker.patch("push.executor.get_backend", return_value=backend)
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        executor = PushExecutor(_Core(), store, _Config())
        sub = store.get(sub_id)
        assert sub is not None
        result = executor.run_subscription(sub)
        assert result["ok"] == 1
        assert len(result["failures"]) == 1
        assert "BAD" in result["failures"][0]
        run = store.last_run(sub_id)
        assert run is not None
        assert run["total"] == 2
        assert run["ok"] == 1

    def test_explicit_stock_000001(self, mocker, tmp_path):
        """显式 kind=stock 解决 000001 歧义：走股票管道而非指数"""
        from push.models import SubscriptionSymbol
        from push.store import PushStore
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(
            name="t",
            symbols=[SubscriptionSymbol(symbol="000001", kind="stock")],
            channel="wecom", time="08:00"))
        backend = _Backend()
        mocker.patch("push.executor.get_backend", return_value=backend)
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        executor = PushExecutor(_Core(), store, _Config())
        sub = store.get(sub_id)
        assert sub is not None
        result = executor.run_subscription(sub)
        assert result["ok"] == 1
        assert "操作信号" in backend.calls[0]["content"]  # 走股票摘要
        assert "指数报告" not in backend.calls[0]["title"]

    def test_explicit_index_000001(self, mocker, tmp_path):
        """显式 kind=index 时 000001 走指数管道（上证指数）"""
        from push.models import SubscriptionSymbol
        from push.store import PushStore
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(
            name="t",
            symbols=[SubscriptionSymbol(symbol="000001", kind="index",
                                        index_style="broad")],
            channel="wecom", time="08:00"))
        backend = _Backend()
        mocker.patch("push.executor.get_backend", return_value=backend)
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        executor = PushExecutor(_Core(), store, _Config())
        sub = store.get(sub_id)
        assert sub is not None
        result = executor.run_subscription(sub)
        assert result["ok"] == 1
        assert "上证指数" in backend.calls[0]["content"]

    def test_invalid_explicit_kind_fails_isolated(self, mocker, tmp_path):
        """显式 kind 校验失败：该标的失败但不影响其他"""
        from push.models import SubscriptionSymbol
        from push.store import PushStore
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(
            name="t",
            symbols=[SubscriptionSymbol(symbol="600519", kind="stock"),
                     SubscriptionSymbol(symbol="ABC123", kind="index")],
            channel="wecom", time="08:00"))
        backend = _Backend()
        mocker.patch("push.executor.get_backend", return_value=backend)
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        executor = PushExecutor(_Core(), store, _Config())
        sub = store.get(sub_id)
        assert sub is not None
        result = executor.run_subscription(sub)
        assert result["ok"] == 1
        assert len(result["failures"]) == 1
        assert "ABC123" in result["failures"][0]
