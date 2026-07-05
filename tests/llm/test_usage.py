import json
from pathlib import Path
from src.llm.usage import UsageLogger


class TestUsageLogger:
    def test_log_writes_json_line(self, tmp_path):
        log_path = tmp_path / "usage.log"
        logger = UsageLogger(log_path)
        logger.log("gpt-4o", 500, 200, 0.003)
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["model"] == "gpt-4o"
        assert entry["prompt_tokens"] == 500
        assert entry["completion_tokens"] == 200

    def test_get_stats_aggregates_correctly(self, tmp_path):
        log_path = tmp_path / "usage.log"
        logger = UsageLogger(log_path)
        logger.log("gpt-4o", 500, 200, 0.005)
        logger.log("gpt-4o", 300, 100, 0.003)
        stats = logger.get_stats()
        assert stats["total_calls"] == 2
        assert stats["total_prompt_tokens"] == 800
        assert stats["total_completion_tokens"] == 300
