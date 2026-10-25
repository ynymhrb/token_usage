"""脱敏辅助函数；此模块不保留转录内容。"""

from __future__ import annotations

import hashlib


def sanitize_identifier(value: str, salt: str) -> str:
    """返回不可逆、可复现的会话标识，不回显输入值。"""
    digest = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()
    return f"id_{digest[:16]}"
