"""信号推导与动作映射 — 回测契约（信号枚举固定，阈值/动作配置化）"""
from dataclasses import dataclass, field
from typing import Literal

from utils.config import Config

Signal = Literal["attack", "watch", "defend"]

SIGNAL_ORDER: tuple[Signal, ...] = ("attack", "watch", "defend")

SIGNAL_LABELS: dict[Signal, str] = {
    "attack": "进攻",
    "watch": "观望",
    "defend": "防御",
}


@dataclass(frozen=True)
class SignalAction:
    action: str
    position: str


DEFAULT_SIGNAL_ACTIONS: dict[Signal, SignalAction] = {
    "attack": SignalAction(action="可考虑建仓/加仓", position="60%-80%"),
    "watch": SignalAction(action="持有观察，等待明确方向", position="30%-50%"),
    "defend": SignalAction(action="减仓或回避", position="0%-20%"),
}


@dataclass(frozen=True)
class SignalConfig:
    thresholds: dict[Signal, float] = field(
        default_factory=lambda: {"attack": 7.0, "watch": 4.0}
    )
    actions: dict[Signal, SignalAction] = field(
        default_factory=lambda: dict(DEFAULT_SIGNAL_ACTIONS)
    )


def derive_signal(final_score: float, thresholds: dict[Signal, float]) -> Signal:
    """由综合得分推导操作信号（确定性规则，可回测）"""
    if final_score >= thresholds["attack"]:
        return "attack"
    if final_score >= thresholds["watch"]:
        return "watch"
    return "defend"


def load_signal_config(config: Config) -> SignalConfig:
    """从配置加载信号阈值与动作映射，校验失败抛 ValueError（回测契约完整性）"""
    raw = config.get("signal")
    if not isinstance(raw, dict):
        raise ValueError("signal 配置缺失或不是字典")  # noqa: TRY004 契约要求 ValueError
    thresholds_raw = raw.get("thresholds")
    if not isinstance(thresholds_raw, dict):
        raise ValueError("signal.thresholds 缺失或不是字典")  # noqa: TRY004 契约要求 ValueError
    actions_raw = raw.get("actions")
    if not isinstance(actions_raw, dict):
        raise ValueError("signal.actions 缺失或不是字典")  # noqa: TRY004 契约要求 ValueError

    thresholds: dict[Signal, float] = {}
    # defend 无独立阈值：得分低于 watch 即防御；键缺失必须抛错（回测契约）
    for key in ("attack", "watch"):
        val = thresholds_raw.get(key)
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(f"signal.thresholds.{key} 缺失或不是数字")  # noqa: TRY004 契约要求 ValueError（测试断言 pytest.raises(ValueError)）
        thresholds[key] = float(val)
    if not (0 < thresholds["watch"] < thresholds["attack"] <= 10):
        raise ValueError("signal.thresholds 非法：需满足 0 < watch < attack <= 10")

    actions: dict[Signal, SignalAction] = {}
    for key in SIGNAL_ORDER:
        item = actions_raw.get(key)
        if not isinstance(item, dict):
            raise ValueError(f"signal.actions.{key} 缺失或不是字典")  # noqa: TRY004 契约要求 ValueError（测试断言 pytest.raises(ValueError)）
        default = DEFAULT_SIGNAL_ACTIONS[key]
        actions[key] = SignalAction(
            action=str(item.get("action") or default.action),
            position=str(item.get("position") or default.position),
        )
    return SignalConfig(thresholds=thresholds, actions=actions)
