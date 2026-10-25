"""在插件受控工具边界压缩模型可见输出。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
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
        marker_path = self._data_dir / f"{digest}.seen"
        duplicate = digest in self._seen or marker_path.exists()
        (self._data_dir / storage_name).write_text(text, encoding="utf-8")
        marker_path.touch(exist_ok=True)
        if duplicate:
            result = OptimizedResult(f"重复结果已省略：{digest}；原始长度 {len(text)} 字符。", storage_name, len(text), True, True)
            self._write_receipt(source, digest, result, failed)
            return result
        self._seen.add(digest)
        visible = self._redact(text)
        if len(text) <= self._max_chars:
            result = OptimizedResult(visible, storage_name, len(text), False, False)
            self._write_receipt(source, digest, result, failed)
            return result
        errors = [self._redact(line.strip()) for line in text.splitlines() if "error" in line.lower() or "failed" in line.lower()]
        summary = f"结果已压缩：原始长度 {len(text)} 字符，完整结果保存为 {storage_name}。"
        if failed and errors:
            summary = f"{summary}\n关键错误：{errors[0]}"
        result = OptimizedResult(summary[:self._max_chars], storage_name, len(text), True, False)
        self._write_receipt(source, digest, result, failed)
        return result

    @staticmethod
    def _redact(text: str) -> str:
        text = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", "[私钥已脱敏]", text, flags=re.IGNORECASE)
        text = re.sub(r"(?i)\b(?:api[_-]?key|token|password|secret|aws_secret_access_key|aws_(?:session_)?token|access[_-]?key|client_secret|private_key)\b\s*[:=]\s*[^\s,;]+", "[凭据已脱敏]", text)
        text = re.sub(r"(?i)(authorization\s*:\s*[a-z][a-z0-9_-]*\s+)[^\s]+", r"\1[已脱敏]", text)
        text = re.sub(r"\b(?:gh[pousr]_\w{20,}|github_pat_\w{20,})\b", "[GitHub 令牌已脱敏]", text)
        text = re.sub(r"(?i)https?://[^\s/@:]+:[^@\s]+@[^\s]+", "[带凭据 URL 已脱敏]", text)
        return re.sub(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s]+", "[连接串已脱敏]", text)

    def _write_receipt(self, source: str, digest: str, result: OptimizedResult, failed: bool) -> None:
        receipt = {"source": source, "content_id": digest, "original_chars": result.original_chars, "compressed": result.compressed, "duplicate": result.duplicate, "failed": failed, "storage_name": result.storage_name, "recorded_at": datetime.now(timezone.utc).isoformat()}
        (self._data_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex}.receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True), encoding="utf-8")
