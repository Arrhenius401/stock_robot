"""验证源码导入边界，并生成沿用项目配置的工作树 Pyright 配置。"""
import argparse
import importlib.util
import json
import os
import sys
import tomllib
from pathlib import Path


def prepare(root: Path, runtime: Path, output: Path) -> dict:
    root, runtime = root.resolve(), runtime.resolve()
    source = root / "src"
    for package in source.iterdir():
        if not (package / "__init__.py").is_file():
            continue
        spec = importlib.util.find_spec(package.name)
        locations = list(spec.submodule_search_locations or []) if spec else []
        if package.resolve() not in [Path(item).resolve() for item in locations]:
            raise RuntimeError(f"源码导入未指向当前工作树：{package.name}: {locations}")
    with (root / "pyproject.toml").open("rb") as stream:
        config = dict(tomllib.load(stream).get("tool", {}).get("pyright", {}))
    # 临时配置改变了相对路径的基准，必须按临时配置所在目录重新定位项目路径。
    for key in ("include", "exclude", "ignore", "strict"):
        if key in config:
            config[key] = [os.path.relpath(root / item, output.parent).replace("\\", "/") for item in config[key]]
    config.setdefault("include", [os.path.relpath(source, output.parent), os.path.relpath(root / "tests", output.parent)])
    config.setdefault("exclude", [os.path.relpath(root / "tmp", output.parent), os.path.relpath(root / ".venv", output.parent), "**/__pycache__", "**/node_modules"])
    config["extraPaths"] = [str(source), str(root), *[str(root / item) for item in config.get("extraPaths", [])]]
    config["venvPath"], config["venv"] = str(runtime.parent), runtime.name
    for environment in config.get("executionEnvironments", []):
        environment["root"] = str(root / environment.get("root", "."))
        environment["extraPaths"] = [str(source), str(root), *[str(root / item) for item in environment.get("extraPaths", [])]]
    output.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"SourceRoot": str(root), "RuntimeRoot": str(runtime), "Python": sys.executable, "PyrightConfig": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.root, arguments.runtime, arguments.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
