"""行业映射表重建 runner — 小批量 + 批间休息 + 失败重试（适配 legulegu 限流）

实测（2026-08-24 验证）：legulegu 限流窗口容量为 8 个请求，每批第 9 个
必 504；批间休息 300s 窗口完全恢复。策略：每批 8 个行业、批间休息 300s，
失败行业最多重试 3 轮。一次性运维脚本，不进测试套件。
"""
import json
import time

from data.industry_mapping_builder import (
    DEFAULT_DELAY,
    _csv_path,
    _load_style_mapping,
    _write_csv,
    fetch_constituents,
    fetch_taxonomy,
)

BATCH_SIZE = 8    # 每批行业数（实测限流窗口容量）
BATCH_REST = 300  # 批间休息秒（窗口完全恢复所需）
RETRY_REST = 15   # 单行业失败后休息秒
ROUND_REST = 300  # 轮间长休息秒
MAX_ROUNDS = 3    # 最大轮数（含首轮）


def _wait_until_available(max_minutes: int = 30) -> None:
    """等待站点限流解除：每 30s 试探 overview 一次，解除后返回"""
    from data.industry_mapping_builder import OVERVIEW_URL, _get_with_retry

    for i in range(max_minutes * 2):
        try:
            _get_with_retry(OVERVIEW_URL, retries=1)
            print("站点可用，开始重建", flush=True)
            return
        except Exception as e:  # noqa: BLE001 — 第三方网络边界异常类型不可预测
            print(f"等待解除 {i * 30}s: {e}", flush=True)
            time.sleep(30)
    raise SystemExit("等待站点恢复超时")


def main():
    _wait_until_available()
    level2_map, level3_map = fetch_taxonomy(refresh=True)
    style_map = _load_style_mapping()
    stock_rows: dict[str, dict] = {}

    pending = list(level3_map.items())
    for round_no in range(1, MAX_ROUNDS + 1):
        if not pending:
            break
        print(f"第 {round_no} 轮：剩余 {len(pending)} 个行业", flush=True)
        failed: list = []
        for i, (code, (name, container_level2)) in enumerate(pending, 1):
            try:
                stocks = fetch_constituents(code)
            except Exception as e:  # noqa: BLE001 — 第三方网络边界异常类型不可预测
                print(f"  失败 {name}({code}): {e}", flush=True)
                failed.append((code, (name, container_level2)))
                time.sleep(RETRY_REST)
                continue
            for s in stocks:
                row_level2 = s.get("level2") or container_level2
                row_level1 = level2_map.get(row_level2, "综合")
                if s["symbol"] not in stock_rows:
                    stock_rows[s["symbol"]] = {
                        "symbol": s["symbol"],
                        "sw_level1": row_level1,
                        "sw_level2": row_level2,
                        "style_category": style_map.get(row_level1, "高端制造"),
                    }
            if i % BATCH_SIZE == 0 and i < len(pending):
                print(f"  进度 {i}/{len(pending)}，休息 {BATCH_REST}s", flush=True)
                time.sleep(BATCH_REST)
            else:
                time.sleep(DEFAULT_DELAY)
        pending = failed
        if pending and round_no < MAX_ROUNDS:
            print(f"第 {round_no} 轮失败 {len(pending)} 个，休息 {ROUND_REST}s 后重试", flush=True)
            time.sleep(ROUND_REST)

    rows = list(stock_rows.values())
    _write_csv(rows)

    known_total: int | None = None
    if _csv_path().exists():
        with open(_csv_path(), encoding="utf-8") as f:
            known_total = sum(1 for _ in f) - 1
    denominator = known_total or len(rows)
    coverage_pct = round(len(rows) / denominator * 100, 1) if denominator else 100.0

    print(json.dumps({
        "stock_count": len(rows),
        "failed_industries": [code for code, _ in pending],
        "coverage_pct": coverage_pct,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
