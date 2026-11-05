#!/usr/bin/env python3
"""Deterministically route usage-analyze arguments without reading transcripts."""

from __future__ import annotations

import json
import re
import sys
from typing import Sequence


VALUE_COUNTS = {
    "--context-threshold": 1,
    "--lang": 1,
    "--claude-home": 1,
    "--search-depth": 1,
    "--top": 1,
    "--timeout": 1,
    "--json-out": 1,
    "--period": 2,
    "--last-hours": 1,
    "--five-hour-at": 1,
}
SWITCHES = {
    "--query-cli",
    "--no-subagents",
    "--statusline-capture",
    "--current-five-hour",
    "--current-session",
    "--list-five-hour-windows",
    "--version",
}
PERIOD_SELECTORS = {
    "--period",
    "--last-hours",
    "--current-five-hour",
    "--current-session",
    "--five-hour-at",
    "--list-five-hour-windows",
    "--statusline-capture",
}
SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
DATETIME_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}(?::\d{2})?)\b")
LAST_HOURS_RE = re.compile(
    r"(?:last|past|最近|过去)\s*(\d+(?:\.\d+)?)\s*(?:hours?|小时)", re.IGNORECASE
)
CURRENT_RE = re.compile(
    r"(?:current|latest|most\s+recent|right\s+now|当前|现在|最近一次|本次)", re.IGNORECASE
)


class RouteError(ValueError):
    pass


def _map_time_text(text: str) -> tuple[list[str], str]:
    timestamps = DATETIME_RE.findall(text)
    hour_matches = LAST_HOURS_RE.findall(text)
    current = bool(CURRENT_RE.search(text))
    intents = int(bool(timestamps)) + int(bool(hour_matches)) + int(current)
    if intents > 1 or len(timestamps) > 1 or len(hour_matches) > 1:
        raise RouteError("ambiguous time description; provide one explicit time range")
    if timestamps:
        value = timestamps[0].replace("T", " ")
        return ["--five-hour-at", value], f'time description -> --five-hour-at "{value}"'
    if hour_matches:
        value = hour_matches[0]
        return ["--last-hours", value], f"time description -> --last-hours {value}"
    if current:
        return ["--current-session"], "time description -> --current-session"
    raise RouteError("unsupported text; provide a supported time description or explicit profiler flags")


def route(arguments: Sequence[str]) -> dict[str, object]:
    args = list(arguments)
    raw_count = args.count("--raw")
    llm_count = args.count("--llm")
    if raw_count > 1 or llm_count > 1 or (raw_count and llm_count):
        raise RouteError("choose exactly one of --raw or --llm")
    mode = "raw" if raw_count else "llm"
    args = [arg for arg in args if arg not in {"--raw", "--llm"}]

    profiler_args: list[str] = []
    text_parts: list[str] = []
    has_selector = False
    has_session = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--llm-package":
            raise RouteError("--llm-package is managed internally by the Skill")
        if arg in VALUE_COUNTS:
            count = VALUE_COUNTS[arg]
            values = args[index + 1:index + 1 + count]
            if len(values) != count or any(value.startswith("--") for value in values):
                raise RouteError(f"{arg} requires {count} value(s)")
            profiler_args.extend([arg, *values])
            has_selector = has_selector or arg in PERIOD_SELECTORS
            index += count + 1
            continue
        if arg in SWITCHES:
            profiler_args.append(arg)
            has_selector = has_selector or arg in PERIOD_SELECTORS
            index += 1
            continue
        if arg.startswith("--"):
            raise RouteError(f"unsupported profiler flag: {arg}")
        if SESSION_ID_RE.fullmatch(arg):
            if has_session or text_parts:
                raise RouteError("session ID cannot be combined with natural-language text")
            profiler_args.append(arg)
            has_session = True
        else:
            text_parts.append(arg)
        index += 1

    mapping = None
    if text_parts:
        if has_selector or has_session:
            raise RouteError("do not combine an explicit selector with natural-language text")
        mapped, mapping = _map_time_text(" ".join(text_parts))
        profiler_args.extend(mapped)
        has_selector = True
    elif not has_selector and not has_session:
        profiler_args.append("--current-session")

    return {
        "mode": mode,
        "profiler_args": profiler_args,
        "mapping": mapping,
        "display_mapping": bool(mapping and mode == "llm"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        print(json.dumps(route(sys.argv[1:] if argv is None else argv), ensure_ascii=False))
    except RouteError as exc:
        print(f"usage-analyze: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
