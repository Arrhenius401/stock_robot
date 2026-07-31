"""行业映射表生成脚本 — 每月运行一次，从 AkShare 拉取全量 A 股行业分类"""
import csv
from pathlib import Path

import yaml


def load_sw_to_style_mapping() -> dict[str, str]:
    config_dir = Path(__file__).parent.parent / "src" / "analysis" / "config"
    mapping_path = config_dir / "申万_大类_映射.yaml"
    with open(mapping_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def generate_mapping():
    """从 AkShare 拉取全量 A 股列表和行业分类，生成本地 CSV。"""
    import akshare as ak

    # 获取全量 A 股列表
    stock_df = ak.stock_info_a_code_name()
    sw_to_style = load_sw_to_style_mapping()

    output_path = Path(__file__).parent.parent / "data" / "industry_mapping.csv"
    rows = []
    for _, row in stock_df.iterrows():
        symbol = row["code"]
        industry = row.get("industry", "")
        sw_level1 = industry if industry else "综合"
        style_category = sw_to_style.get(sw_level1, "高端制造")

        rows.append({
            "symbol": symbol,
            "sw_level1": sw_level1,
            "sw_level2": "",
            "style_category": style_category,
        })

    # with语句用于专门用来安全打开资源，用完自动释放资源
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "sw_level1", "sw_level2", "style_category"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"已生成 {len(rows)} 条映射记录 → {output_path}")


if __name__ == "__main__":
    generate_mapping()
