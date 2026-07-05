"""LLM Token 用量日志记录"""
import json
import time
from pathlib import Path


class UsageLogger:
    def __init__(self, log_path: Path):
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, model: str, prompt_tokens: int, completion_tokens: int, cost_estimate: float = 0.0):
        entry = {
            "timestamp": time.time(),
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_estimate": cost_estimate,
        }
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_stats(self) -> dict:
        if not self._log_path.exists():
            return {"total_calls": 0, "total_prompt_tokens": 0, "total_completion_tokens": 0}
        calls = 0
        prompt_total = 0
        completion_total = 0
        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    calls += 1
                    prompt_total += entry.get("prompt_tokens", 0)
                    completion_total += entry.get("completion_tokens", 0)
        return {
            "total_calls": calls,
            "total_prompt_tokens": prompt_total,
            "total_completion_tokens": completion_total,
        }
