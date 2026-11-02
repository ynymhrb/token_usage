"""从已保存的请求结构统计上下文构成，不保留任何请求正文。"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .adapter import discover_rollouts
from .models import DataGap


EXPORT_SUFFIX = ".llm_calls.jsonl"
CATEGORY_ORDER = (
    "static_instructions",
    "tool_definitions",
    "developer_messages",
    "user_messages",
    "assistant_history",
    "tool_arguments",
    "tool_results",
    "unattributable",
)
CATEGORY_LABELS = {
    "static_instructions": "静态指令（系统、项目规则与 Skill 的合并结果）",
    "tool_definitions": "工具定义",
    "developer_messages": "开发者消息",
    "user_messages": "用户消息",
    "assistant_history": "助手历史",
    "tool_arguments": "工具调用参数",
    "tool_results": "工具结果",
    "unattributable": "不可归因项（推理、压缩点或未知项）",
}


@dataclass(frozen=True)
class ContextAttribution:
    """只包含计数和覆盖信息，刻意不携带请求正文。"""

    call_count: int
    category_chars: Mapping[str, int]
    data_gaps: tuple[DataGap, ...]
    discovered_rollout_count: int = 0
    rollouts_with_request_structure: int = 0


def discover_context_exports(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(path for path in root.rglob(f"*{EXPORT_SUFFIX}") if path.is_file())


def _rollout_key(path: Path, root: Path, suffix: str) -> str:
    """用相对路径和去掉格式后缀的基名关联导出，不保留绝对路径。"""
    relative = path.relative_to(root)
    return str(relative.with_name(relative.name.removesuffix(suffix)))


def _serialized_chars(value: object) -> int:
    """以稳定 JSON 长度作为无需分词器的可审计结构代理。"""
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _item_category(item: Mapping[str, Any]) -> str:
    item_type = item.get("type")
    if item_type == "message":
        role = item.get("role")
        if role == "developer":
            return "developer_messages"
        if role == "user":
            return "user_messages"
        if role == "assistant":
            return "assistant_history"
        return "unattributable"
    if isinstance(item_type, str) and item_type.endswith("_output"):
        return "tool_results"
    if item_type in {"function_call", "web_search_call", "tool_search_call", "computer_call", "custom_tool_call"}:
        return "tool_arguments"
    return "unattributable"


def _classify_request(request: Mapping[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    if "instructions" in request:
        counts["static_instructions"] += _serialized_chars(request["instructions"])
    if "persisted_dynamic_tools" in request:
        counts["tool_definitions"] += _serialized_chars(request["persisted_dynamic_tools"])
    input_items = request.get("input")
    if isinstance(input_items, list):
        for item in input_items:
            if isinstance(item, Mapping):
                counts[_item_category(item)] += _serialized_chars(item)
            else:
                counts["unattributable"] += _serialized_chars(item)
    elif input_items is not None:
        counts["unattributable"] += _serialized_chars(input_items)
    return counts


def analyze_context_exports(
    root: Path,
    *,
    export_paths: Iterable[Path] | None = None,
    rollout_paths: Iterable[Path] | None = None,
) -> ContextAttribution:
    """汇总请求导出；坏行和缺失导出均显式转化为数据缺口。"""
    counts: Counter[str] = Counter({category: 0 for category in CATEGORY_ORDER})
    gaps: list[DataGap] = []
    call_count = 0
    paths = list(export_paths) if export_paths is not None else discover_context_exports(root)
    rollouts = list(rollout_paths) if rollout_paths is not None else discover_rollouts(root)
    rollout_keys = {
        _rollout_key(path, root, ".jsonl.zst" if path.name.endswith(".jsonl.zst") else path.suffix)
        for path in rollouts
    }
    export_keys = {_rollout_key(path, root, EXPORT_SUFFIX) for path in paths}
    covered_rollout_count = len(rollout_keys & export_keys)
    missing_rollout_count = len(rollout_keys - export_keys)
    if not paths:
        gaps.append(DataGap("missing_request_structure", "没有保存的完整请求结构，无法归因上下文类别", ()))
    elif missing_rollout_count:
        gaps.append(DataGap(
            "incomplete_request_structure",
            f"有 {missing_rollout_count} 个已发现 rollout 缺少对应请求结构，类别占比不是全量覆盖",
            (),
        ))
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            gaps.append(DataGap("unreadable_request_export", "请求结构导出无法读取", ()))
            continue
        for line_number, line in enumerate(lines, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                gaps.append(DataGap("invalid_request_export", "存在无法解析的请求结构导出行", (line_number,)))
                continue
            if not isinstance(record, Mapping) or not isinstance(record.get("request"), Mapping):
                gaps.append(DataGap("invalid_request_export", "请求结构导出行缺少 request 对象", (line_number,)))
                continue
            counts.update(_classify_request(record["request"]))
            call_count += 1
    return ContextAttribution(
        call_count,
        dict(counts),
        tuple(gaps),
        discovered_rollout_count=len(rollout_keys),
        rollouts_with_request_structure=covered_rollout_count,
    )


def context_report(attribution: ContextAttribution) -> dict[str, object]:
    """返回不含原文、可直接序列化的上下文构成报告。"""
    total_chars = sum(attribution.category_chars.values())
    categories = []
    for key in CATEGORY_ORDER:
        chars = attribution.category_chars.get(key, 0)
        categories.append({
            "category": key,
            "label": CATEGORY_LABELS[key],
            "character_count": chars,
            "share_of_observed_request_chars": round(chars / total_chars, 6) if total_chars else None,
            "evidence": "请求结构字符代理；不是精确 Token 账单",
        })
    recommendations = []
    actions = {
        "static_instructions": "优先精简合并静态指令；当前数据不能拆出系统提示、项目规则和 Skill 各自的精确占用。",
        "tool_definitions": "优先收窄常驻工具定义，只向当前任务暴露必要的工具和字段。",
        "tool_results": "优先压缩历史工具结果：保留结论、路径和可复现命令，将大输出转为摘要或文件引用。",
        "tool_arguments": "检查重复或过大的工具参数，避免把已知上下文再次完整传入工具。",
        "assistant_history": "将已完成阶段的助手历史归纳为短摘要，保留决策、约束和未完成事项。",
        "user_messages": "合并重复需求描述，保留最新的明确约束与验收标准。",
        "developer_messages": "合并重复的开发者级规则，删除与当前任务无关的长说明。",
        "unattributable": "存在推理、压缩点或未知输入项；需先补齐运行时导出，不能安全地给出删除建议。",
    }
    if attribution.call_count:
        for category in categories:
            if category["share_of_observed_request_chars"] is not None and category["share_of_observed_request_chars"] >= 0.2:
                recommendations.append({
                    "category": category["category"],
                    "summary": actions[category["category"]],
                    "evidence": f"在 {attribution.call_count} 次已保存请求中占观察到请求字符的 {category['share_of_observed_request_chars']:.1%}。",
                })
    return {
        "schema_version": 1,
        "scope": {
            "saved_request_call_count": attribution.call_count,
            "discovered_rollout_count": attribution.discovered_rollout_count,
            "rollouts_with_request_structure": attribution.rollouts_with_request_structure,
            "rollouts_without_request_structure": (
                attribution.discovered_rollout_count - attribution.rollouts_with_request_structure
            ),
            "exact_token_attribution": "不可得：当前请求结构导出没有可验证的一对一 Token 使用记录。",
            "measurement": "按稳定 JSON 序列化字符数归类；可比较占比，不可作为账单 Token。",
        },
        "categories": categories,
        "data_gaps": [{"code": gap.code, "summary": gap.summary, "lines": list(gap.evidence_lines)} for gap in attribution.data_gaps],
        "recommendations": recommendations,
    }
