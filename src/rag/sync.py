"""本地报告自动同步 — 监听 reports/ 目录 → 自动 chunk → embed → upsert"""
import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_SYMBOL_IN_TITLE = re.compile(r"（(\d{6})）|\((\d{6})\)")


class LocalReportSync:
    def __init__(self, reports_dir: str, engine=None):
        self.reports_dir = reports_dir
        self._engine = engine

    def sync(self) -> dict:
        processed = 0
        skipped = 0
        failed = 0

        engine = self._engine
        if engine is None:
            return {"processed": 0, "skipped": 0, "failed": 0, "note": "引擎未注入"}

        new_files = self.scan_new_files()
        for file_info in new_files:
            try:
                content = Path(file_info["path"]).read_text(encoding="utf-8")
                symbols = self.extract_symbols_from_content(content)
                date = file_info.get("date", "")

                if not symbols:
                    symbols_from_name = self._symbols_from_filename(file_info["name"])
                    symbols = symbols_from_name

                result = engine.ingest_file(
                    file_path=file_info["path"],
                    source_type="history_reports",
                    title=file_info["name"].replace(".md", ""),
                    date=date,
                    symbols=symbols,
                    tags=["历史报告"],
                )
                if result.get("status") == "success":
                    processed += 1
                elif result.get("status") == "skipped":
                    skipped += 1
                else:
                    failed += 1
                    logger.warning("报告同步失败: %s", file_info["path"])
            except Exception as e:  # noqa: BLE001 — 单文件同步失败不阻断批量
                failed += 1
                logger.error("同步报告异常: %s → %s", file_info.get("path", "?"), e)

        return {"processed": processed, "skipped": skipped, "failed": failed}

    def scan_new_files(self, ingested_hashes: set | None = None) -> list[dict]:
        reports_path = Path(self.reports_dir)
        if not reports_path.is_dir():
            logger.warning("报告目录不存在: %s", self.reports_dir)
            return []

        known_hashes = ingested_hashes or self._load_ingested_hashes()
        new_files = []

        for file_path in sorted(reports_path.glob("*.md")):
            content = file_path.read_text(encoding="utf-8")
            file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if file_hash in known_hashes:
                continue

            info = self.extract_report_info(file_path.name)
            new_files.append({
                "path": str(file_path),
                "name": file_path.name,
                "hash": file_hash,
                "date": info.get("date"),
                "symbols": info.get("symbols", []),
            })

        return new_files

    def extract_report_info(self, filename: str) -> dict:
        stem = filename.replace(".md", "")
        parts = stem.split("_")
        info = {"symbols": [], "date": None}
        if len(parts) >= 1 and parts[0].isdigit() and len(parts[0]) == 6:
            info["symbols"] = [parts[0]]
        if len(parts) >= 2 and len(parts[1]) == 8:
            date_str = parts[1]
            info["date"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        return info

    def extract_symbols_from_content(self, content: str) -> list[str]:
        symbols = set()
        for line in content.splitlines()[:3]:
            for m in _SYMBOL_IN_TITLE.finditer(line):
                symbol = m.group(1) or m.group(2)
                if symbol:
                    symbols.add(symbol)
        return list(symbols)

    def _symbols_from_filename(self, filename: str) -> list[str]:
        parts = filename.replace(".md", "").split("_")
        if parts and parts[0].isdigit() and len(parts[0]) == 6:
            return [parts[0]]
        return []

    def _load_ingested_hashes(self) -> set:
        if self._engine is None:
            return set()
        try:
            sources = self._engine.list_sources()
            return {s["source_hash"] for s in sources
                    if s.get("collection") == "history_reports"}
        except Exception:  # noqa: BLE001 — 已入库哈希加载失败视为空集
            return set()
