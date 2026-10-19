"""构建时复制唯一来源的根目录资源，不在源码树维护重复副本。"""
from pathlib import Path

from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self) -> None:
        super().run()
        root = Path(__file__).resolve().parents[2]
        target = Path(self.build_lib) / "stock_robot/resources"
        # 精确白名单避免将本地凭据、候选映射、报告或缓存带入发布包。
        resources = [*sorted((root / "config").rglob("*.yaml"))]
        resources.extend(root / "data" / name for name in (
            "industry_mapping.csv", "eastmoney_sw_mapping.csv", "index_mapping.csv", "etf_index_mapping.csv",
        ))
        resources.append(root / "scripts/run-radar-collector.py")
        for source in resources:
            destination = target / source.relative_to(root)
            self.mkpath(str(destination.parent))
            self.copy_file(str(source), str(destination))
