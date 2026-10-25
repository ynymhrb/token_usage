"""在插件受控工具边界压缩模型可见输出。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OptimizedResult:
    visible_text: str
    storage_name: str
    original_chars: int
    compressed: bool
    duplicate: bool


class ContextOptimizer:
    """完整结果只落本地；模型上下文仅接收预算内的摘要。"""

    def __init__(self, data_dir: Path, *, max_chars: int = 4000) -> None:
        if max_chars < 80:
            raise ValueError("max_chars 不能小于 80")
        self._data_dir = data_dir
        self._max_chars = max_chars
        self._seen: set[str] = set()

    def optimize(self, text: str, *, source: str, failed: bool = False) -> OptimizedResult:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        storage_name = f"{source}-{digest}.txt"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        (self._data_dir / storage_name).write_text(text, encoding="utf-8")
        if digest in self._seen:
            return OptimizedResult(f"重复结果已省略：{digest}；原始长度 {len(text)} 字符。", storage_name, len(text), True, True)
        self._seen.add(digest)
        if len(text) <= self._max_chars:
            return OptimizedResult(text, storage_name, len(text), False, False)
        errors = [line.strip() for line in text.splitlines() if "error" in line.lower() or "failed" in line.lower()]
        summary = f"结果已压缩：原始长度 {len(text)} 字符，完整结果保存为 {storage_name}。"
        if failed and errors:
            summary = f"{summary}\n关键错误：{errors[0]}"
        return OptimizedResult(summary[:self._max_chars], storage_name, len(text), True, False)
