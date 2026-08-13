# Claude Code Session Token Profiler v2.6.0
#!/usr/bin/env python3
"""
Claude Code session token profiler.

Given a Claude Code session ID, this script:
  1) locates ~/.claude/projects/**/<session-id>.jsonl
  2) optionally invokes Claude Code's local /context all and /usage commands
  3) parses the main JSONL plus session subagent transcripts
  4) deduplicates streaming assistant records by requestId
  5) reports per-request cache/input/output usage and likely context-growth sources

No third-party Python packages are required.
"""

from __future__ import annotations

__version__ = "2.6.0"

import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
NUMBER_RE = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>[kKmM]?)")
DEFAULT_CONTEXT_THRESHOLDS = (100_000, 200_000, 500_000)


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s).replace("\r", "")


def human_tokens(n: int | float) -> str:
    n = float(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def parse_human_number(s: str) -> Optional[int]:
    m = NUMBER_RE.search(s.strip())
    if not m:
        return None
    n = float(m.group("n"))
    unit = m.group("unit").lower()
    if unit == "k":
        n *= 1_000
    elif unit == "m":
        n *= 1_000_000
    return int(n)


def file_fingerprint(path: Path) -> Tuple[int, int, str]:
    try:
        st = path.stat()
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return st.st_size, st.st_mtime_ns, h.hexdigest()
    except FileNotFoundError:
        return 0, 0, ""


def locate_session(session_id: str, claude_home: Path, max_depth: int = 2) -> Path:
    projects = claude_home / "projects"
    if not projects.exists():
        raise FileNotFoundError(f"Claude projects directory not found: {projects}")

    target = f"{session_id}.jsonl"
    exact: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(projects):
        current = Path(dirpath)
        try:
            depth = len(current.relative_to(projects).parts)
        except ValueError:
            continue
        if depth >= max_depth:
            dirnames[:] = []
        if target in filenames:
            candidate = current / target
            if not candidate.name.startswith("agent-"):
                exact.append(candidate)

    if not exact:
        raise FileNotFoundError(
            f"Session {session_id!r} not found under {projects} within depth {max_depth}."
        )
    if len(exact) == 1:
        return exact[0]
    exact.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
    return exact[0]

def load_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], List[Tuple[int, str]]]:
    rows: List[Dict[str, Any]] = []
    errors: List[Tuple[int, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    obj["__line__"] = lineno
                    rows.append(obj)
            except json.JSONDecodeError as e:
                # A live session can leave the final line temporarily incomplete.
                errors.append((lineno, str(e)))
    return rows, errors


def detect_cwd(rows: List[Dict[str, Any]], fallback: Path) -> Path:
    for row in rows:
        cwd = row.get("cwd")
        if isinstance(cwd, str) and cwd:
            p = Path(cwd).expanduser()
            if p.is_dir():
                return p
    return fallback


def find_subagent_files(main_path: Path, session_id: str) -> List[Path]:
    candidates: List[Path] = []

    # Current layout commonly stores them at:
    #   <project>/<session-id>/subagents/agent-*.jsonl
    session_dir = main_path.parent / session_id
    if session_dir.exists():
        candidates.extend(session_dir.rglob("agent-*.jsonl"))

    # Older/alternate layouts can put agent files elsewhere under the project.
    # Limit this fallback to files whose parent path mentions this session ID.
    if not candidates:
        for p in main_path.parent.rglob("agent-*.jsonl"):
            if session_id in str(p.parent):
                candidates.append(p)

    # Stable de-duplication.
    return sorted(set(p.resolve() for p in candidates if p.is_file()))


def get_usage(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    msg = row.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
        return msg["usage"]
    if isinstance(row.get("usage"), dict):
        return row["usage"]
    return None


def usage_int(usage: Dict[str, Any], key: str) -> int:
    v = usage.get(key, 0)
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    return 0


def cache_ttl_breakdown(usage: Dict[str, Any]) -> Tuple[int, int]:
    c = usage.get("cache_creation")
    if not isinstance(c, dict):
        return 0, 0
    return (
        int(c.get("ephemeral_5m_input_tokens") or 0),
        int(c.get("ephemeral_1h_input_tokens") or 0),
    )


def request_key(row: Dict[str, Any], index: int) -> str:
    # requestId is the most useful de-duplication key in Claude Code transcripts.
    for key in ("requestId", "request_id"):
        v = row.get(key)
        if isinstance(v, str) and v:
            return v
    msg = row.get("message")
    if isinstance(msg, dict):
        mid = msg.get("id")
        if isinstance(mid, str) and mid:
            return f"message:{mid}"
    # Last resort: do NOT accidentally merge unrelated calls.
    return f"row:{index}:{row.get('__line__', index)}"


def model_name(row: Dict[str, Any]) -> str:
    msg = row.get("message")
    if isinstance(msg, dict):
        m = msg.get("model")
        if isinstance(m, str):
            return m
    m = row.get("model")
    return m if isinstance(m, str) else "?"


def json_compact(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return str(obj)


def approx_tokens_from_text(s: str) -> int:
    """Heuristic only. Claude's exact tokenizer is not available offline here."""
    if not s:
        return 0
    # Mixed code/English/CJK heuristic. Deliberately conservative and simple.
    cjk = sum(1 for ch in s if "\u3400" <= ch <= "\u9fff")
    non_cjk = max(0, len(s) - cjk)
    return max(1, int(round(cjk * 1.15 + non_cjk / 4.0)))


def content_text_size(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return approx_tokens_from_text(content)
    return approx_tokens_from_text(json_compact(content))


def block_signature(prefix: str, block: Any) -> str:
    raw = prefix + "\0" + json_compact(block)
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()


def extract_dynamic_categories(
    row: Dict[str, Any], tool_names: Dict[str, str], seen_blocks: set[str]
) -> collections.Counter[str]:
    """Estimate transcript content introduced by one JSONL row.

    This does NOT include hidden system prompt/tool schemas. Those are supplied by /context.
    Duplicate streaming blocks are suppressed by a content signature.
    """
    out: collections.Counter[str] = collections.Counter()
    typ = str(row.get("type") or "other")
    msg = row.get("message")

    if not isinstance(msg, dict):
        # Some metadata/attachment rows have useful payload outside message.
        if typ in {"system", "attachment", "progress"}:
            payload = {k: v for k, v in row.items() if not k.startswith("__")}
            sig = block_signature(typ, payload)
            if sig not in seen_blocks:
                seen_blocks.add(sig)
                tok = content_text_size(payload)
                if tok:
                    out[f"meta:{typ}"] += tok
        return out

    role = msg.get("role") or typ
    content = msg.get("content")
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = content
    else:
        blocks = []

    for block in blocks:
        if not isinstance(block, dict):
            sig = block_signature(str(role), block)
            if sig in seen_blocks:
                continue
            seen_blocks.add(sig)
            out[f"{role}:other"] += content_text_size(block)
            continue

        btype = str(block.get("type") or "other")
        # Signature uses message ID + block payload where possible, which filters
        # repeated streaming snapshots while preserving genuinely new blocks.
        mid = str(msg.get("id") or row.get("uuid") or row.get("__line__"))
        sig = block_signature(f"{mid}:{btype}", block)
        if sig in seen_blocks:
            continue
        seen_blocks.add(sig)

        if btype == "tool_use":
            tool_id = block.get("id")
            name = str(block.get("name") or "unknown")
            if isinstance(tool_id, str):
                tool_names[tool_id] = name
            tok = content_text_size(block.get("input")) + approx_tokens_from_text(name)
            out[f"tool_use:{name}"] += tok
        elif btype == "tool_result":
            tool_id = block.get("tool_use_id")
            name = tool_names.get(str(tool_id), "unknown")
            tok = content_text_size(block.get("content"))
            out[f"tool_result:{name}"] += tok
        elif btype in {"thinking", "redacted_thinking"}:
            text = block.get("thinking") or block.get("text") or block
            out["assistant:thinking"] += content_text_size(text)
        elif btype == "text":
            text = block.get("text", "")
            if role == "assistant" or typ == "assistant":
                out["assistant:text"] += content_text_size(text)
            elif role == "user" or typ == "user":
                out["user:text"] += content_text_size(text)
            else:
                out[f"{role}:text"] += content_text_size(text)
        elif btype in {"image", "document"}:
            # Serialized bytes/base64 are not a valid token estimate; mark presence.
            out[f"{role}:{btype}"] += 1
        else:
            out[f"{role}:{btype}"] += content_text_size(block)

    return out


@dataclass
class ApiCall:
    actor: str
    request_id: str
    line: int
    timestamp: str = ""
    model: str = "?"
    input_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output_tokens: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    preceding_est: collections.Counter[str] = field(default_factory=collections.Counter)

    @property
    def context_input(self) -> int:
        return self.input_tokens + self.cache_read + self.cache_write

    @property
    def traffic(self) -> int:
        return self.context_input + self.output_tokens


@dataclass
class TranscriptAnalysis:
    actor: str
    path: Path
    calls: List[ApiCall]
    parse_errors: List[Tuple[int, str]]
    estimated_categories_total: collections.Counter[str]


def analyze_transcript(path: Path, actor: str) -> TranscriptAnalysis:
    rows, errors = load_jsonl(path)
    calls_by_id: "collections.OrderedDict[str, ApiCall]" = collections.OrderedDict()
    pending: collections.Counter[str] = collections.Counter()
    total_est: collections.Counter[str] = collections.Counter()
    seen_blocks: set[str] = set()
    tool_names: Dict[str, str] = {}

    for i, row in enumerate(rows):
        usage = get_usage(row)
        is_assistant_usage = usage is not None and (
            row.get("type") == "assistant"
            or (isinstance(row.get("message"), dict) and row["message"].get("role") == "assistant")
        )

        if is_assistant_usage:
            rid = request_key(row, i)
            if rid not in calls_by_id:
                c5, c1 = cache_ttl_breakdown(usage)
                call = ApiCall(
                    actor=actor,
                    request_id=rid,
                    line=int(row.get("__line__") or 0),
                    timestamp=str(row.get("timestamp") or ""),
                    model=model_name(row),
                    input_tokens=usage_int(usage, "input_tokens"),
                    cache_read=usage_int(usage, "cache_read_input_tokens"),
                    cache_write=usage_int(usage, "cache_creation_input_tokens"),
                    output_tokens=usage_int(usage, "output_tokens"),
                    cache_write_5m=c5,
                    cache_write_1h=c1,
                    preceding_est=pending.copy(),
                )
                calls_by_id[rid] = call
                pending.clear()
            else:
                # Streaming duplicates often repeat cache/input counts, while output
                # can be a later snapshot. MAX is safer than SUM for one requestId.
                call = calls_by_id[rid]
                call.input_tokens = max(call.input_tokens, usage_int(usage, "input_tokens"))
                call.cache_read = max(call.cache_read, usage_int(usage, "cache_read_input_tokens"))
                call.cache_write = max(call.cache_write, usage_int(usage, "cache_creation_input_tokens"))
                call.output_tokens = max(call.output_tokens, usage_int(usage, "output_tokens"))
                c5, c1 = cache_ttl_breakdown(usage)
                call.cache_write_5m = max(call.cache_write_5m, c5)
                call.cache_write_1h = max(call.cache_write_1h, c1)
                if call.model == "?":
                    call.model = model_name(row)

        added = extract_dynamic_categories(row, tool_names, seen_blocks)
        pending.update(added)
        total_est.update(added)

    return TranscriptAnalysis(
        actor=actor,
        path=path,
        calls=list(calls_by_id.values()),
        parse_errors=errors,
        estimated_categories_total=total_est,
    )


def run_claude_local_command(
    claude_bin: str, cwd: Path, session_id: str, slash_command: str, timeout: int
) -> Tuple[bool, str, str, int]:
    cmd = [
        claude_bin,
        "--resume",
        session_id,
        "--print",
        "--output-format",
        "text",
        slash_command,
    ]
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=os.environ.copy(),
        )
        out = strip_ansi(p.stdout).strip()
        err = strip_ansi(p.stderr).strip()
        return p.returncode == 0 and bool(out), out, err, p.returncode
    except subprocess.TimeoutExpired as e:
        out = strip_ansi((e.stdout or "") if isinstance(e.stdout, str) else "")
        err = strip_ansi((e.stderr or "") if isinstance(e.stderr, str) else "")
        return False, out.strip(), (err + f"\nTimed out after {timeout}s").strip(), 124
    except OSError as e:
        return False, "", str(e), 127


def parse_context_output(text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"raw": text, "categories": {}}
    m = re.search(r"(?:\*\*Tokens:\*\*\s*)?([\d.]+\s*[kKmM]?)\s*/\s*([\d.]+\s*[kKmM]?)\s*(?:tokens)?", text, re.I)
    if m:
        result["used_tokens"] = parse_human_number(m.group(1))
        result["window_tokens"] = parse_human_number(m.group(2))

    category_names = [
        "System prompt", "System tools", "Memory files", "Skills",
        "Messages", "Free space", "Autocompact buffer",
    ]
    for name in category_names:
        patterns = [
            rf"{re.escape(name)}:\s*([\d.]+\s*[kKmM]?)\s+tokens",
            rf"\|\s*{re.escape(name)}\s*\|\s*([\d.]+\s*[kKmM]?)\s*\|",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                result["categories"][name] = parse_human_number(m.group(1))
                break
    return result

def print_rule(ch: str = "-") -> None:
    print(ch * 100)


def print_live_section(name: str, ok: bool, out: str, err: str) -> None:
    print(f"\n[{name}]")
    print_rule()
    if ok:
        print(out)
    else:
        print("Unavailable via non-interactive CLI on this installation.")
        if out:
            print("stdout:")
            print(out)
        if err:
            print("stderr:")
            print(err)


def top_counter(c: collections.Counter[str], n: int = 3) -> str:
    if not c:
        return "-"
    return ", ".join(f"{k}~{human_tokens(v)}" for k, v in c.most_common(n))


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        x = float(value)
        if x > 10_000_000_000:
            x /= 1000.0
        try:
            return datetime.fromtimestamp(x, tz=timezone.utc)
        except Exception:
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt
    except ValueError:
        return None


def fmt_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "-"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def fmt_short_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "-"
    return dt.astimezone().strftime("%m-%d %H:%M:%S")


def duration_text(seconds: float, lang: str) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if lang == "zh":
        parts = []
        if d: parts.append(f"{d}天")
        if h: parts.append(f"{h}小时")
        if m: parts.append(f"{m}分")
        if sec or not parts: parts.append(f"{sec}秒")
        return " ".join(parts)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if sec or not parts: parts.append(f"{sec}s")
    return " ".join(parts)


def call_dt(c: ApiCall) -> Optional[datetime]:
    return parse_dt(c.timestamp)


def is_full_rewrite(c: ApiCall) -> bool:
    ctx = c.context_input
    if ctx < 100_000 or c.cache_write < 100_000:
        return False
    return c.cache_write >= int(ctx * 0.70) and c.cache_read <= max(10_000, int(ctx * 0.08))


def cache_rewrite_details(c: ApiCall) -> Dict[str, Any]:
    """Return explainable cache-rewrite metrics for one API call."""
    ctx = c.context_input
    if c.cache_write_1h and c.cache_write_5m:
        ttl = "mixed"
    elif c.cache_write_1h:
        ttl = "1h"
    elif c.cache_write_5m:
        ttl = "5m"
    else:
        ttl = "unknown" if c.cache_write else "none"
    full_rewrite = is_full_rewrite(c)
    if not full_rewrite:
        likely_cause = "incremental-cache-write" if c.cache_write else "none"
    elif ttl == "1h":
        likely_cause = "1h-cache-miss-or-expiry"
    elif ttl == "5m":
        likely_cause = "5m-cache-miss-or-expiry"
    else:
        likely_cause = "cold-start-or-cache-invalidation"
    return {
        "is_full_rewrite": full_rewrite,
        "rewrite_ratio": (c.cache_write / ctx) if ctx else 0.0,
        "ttl": ttl,
        "likely_cause": likely_cause,
        "cache_write_5m": c.cache_write_5m,
        "cache_write_1h": c.cache_write_1h,
    }


def call_timeline_record(c: ApiCall) -> Dict[str, Any]:
    details = cache_rewrite_details(c)
    actor_session = c.actor.split(":", 1)[-1].split("/", 1)[0]
    return {
        "timestamp": fmt_dt(call_dt(c)),
        "session_id": actor_session,
        "actor": c.actor,
        "model": c.model,
        "request_id": c.request_id,
        "input_tokens": c.input_tokens,
        "cache_read_input_tokens": c.cache_read,
        "cache_creation_input_tokens": c.cache_write,
        "output_tokens": c.output_tokens,
        "context_input_tokens": c.context_input,
        "full_cache_rewrite": details["is_full_rewrite"],
        "rewrite_ratio": details["rewrite_ratio"],
        "cache_ttl": details["ttl"],
        "likely_rewrite_cause": details["likely_cause"],
        "measurement": "exact-jsonl-usage",
    }


CO_CREATOR_LINE_RE = re.compile(
    r"^\[[^\]\r\n]+\]\s+\[[^\]\r\n]*\bco-creator\]\s*(?P<text>.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def truncate_title(text: str, max_chars: int = 100) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 1)].rstrip() + "…"


def user_turn_title(row: Dict[str, Any], max_chars: int = 100) -> str:
    """Extract a human-facing title from a real user-input transcript row."""
    if row.get("type") != "user" or row.get("sourceToolAssistantUUID"):
        return ""
    msg = row.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return ""
    content = msg.get("content")
    texts: List[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    texts.append(value)
    raw = "\n".join(texts).strip()
    if not raw:
        return ""
    # Collaboration wrappers carry the actual human message near the end of a
    # synthesized dispatch prompt. Prefer that text over the wrapper boilerplate.
    embedded = list(CO_CREATOR_LINE_RE.finditer(raw))
    if embedded:
        raw = embedded[-1].group("text")
    return truncate_title(raw, max_chars)


def aggregate_conversation_turns(
    main_rows: List[Dict[str, Any]], calls: Iterable[ApiCall],
    start: datetime, end: datetime,
) -> List[Dict[str, Any]]:
    """Assign main and subagent API calls to the latest main-session user input."""
    anchors: List[Tuple[datetime, str]] = []
    for row in main_rows:
        title = user_turn_title(row)
        timestamp = parse_dt(row.get("timestamp"))
        if title and timestamp and start <= timestamp < end:
            anchors.append((timestamp, title))
    anchors.sort(key=lambda item: item[0])

    buckets: "collections.OrderedDict[Tuple[Optional[datetime], str], List[ApiCall]]" = collections.OrderedDict()
    for call in sorted(calls, key=lambda c: call_dt(c) or start):
        timestamp = call_dt(call)
        if timestamp is None or not (start <= timestamp < end):
            continue
        anchor = next((item for item in reversed(anchors) if item[0] <= timestamp), None)
        key = anchor if anchor else (None, "会话恢复/后台继续")
        buckets.setdefault(key, []).append(call)

    turns: List[Dict[str, Any]] = []
    for index, ((anchor_at, title), turn_calls) in enumerate(buckets.items(), 1):
        times = [call_dt(c) for c in turn_calls if call_dt(c)]
        rewrites = [c for c in turn_calls if is_full_rewrite(c)]
        dynamic: collections.Counter[str] = collections.Counter()
        for call in turn_calls:
            dynamic.update(call.preceding_est)
        turns.append({
            "round": index,
            "title": title,
            "user_input_at": anchor_at,
            "activity_start": min(times) if times else None,
            "activity_end": max(times) if times else None,
            "calls": len(turn_calls),
            "processed": sum(c.context_input for c in turn_calls),
            "cache_read": sum(c.cache_read for c in turn_calls),
            "cache_write": sum(c.cache_write for c in turn_calls),
            "output": sum(c.output_tokens for c in turn_calls),
            "peak_context": max((c.context_input for c in turn_calls), default=0),
            "rewrites": len(rewrites),
            "rewrite_tokens": sum(c.cache_write for c in rewrites),
            "thinking": int(dynamic.get("assistant:thinking", 0)),
        })
    return turns


def dynamic_label(name: str, lang: str) -> str:
    if lang != "zh":
        return name
    if name == "assistant:thinking": return "Claude 思考（assistant:thinking）"
    if name == "assistant:text": return "Claude 正文（assistant:text）"
    if name == "user:text": return "用户文本（user:text）"
    if name.startswith("tool_result:"): return f"工具结果（{name.split(':',1)[1]}）"
    if name.startswith("tool_use:"): return f"工具调用（{name.split(':',1)[1]}）"
    if name.startswith("meta:"): return f"元数据（{name.split(':',1)[1]}）"
    return name


def actor_label(actor: str, lang: str) -> str:
    if lang == "zh" and actor == "main":
        return "主会话"
    return actor


def find_structured_five_hour(obj: Any, out: List[Dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        fh = obj.get("five_hour")
        if isinstance(fh, dict):
            reset = fh.get("resets_at") or fh.get("reset_at")
            used = fh.get("used_percentage") or fh.get("usage_percentage")
            if reset is not None:
                out.append({"resets_at": reset, "used_percentage": used})
        for v in obj.values():
            find_structured_five_hour(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_structured_five_hour(v, out)


LIMIT_HIT_RE = re.compile(
    r"(?:you(?:'|’)ve|you have)\s+hit\s+your\s+session\s+limit|"
    r"5\s*[- ]?hour\s+limit\s+(?:reached|hit)", re.IGNORECASE
)
LIMIT_CONTEXT_RE = re.compile(
    r"session\s+limit|5\s*[- ]?hour\s+limit|current\s+session", re.IGNORECASE
)
RESET_TEXT_RE = re.compile(
    r"\bresets?\s*(?:at\s*)?(?P<spec>"
    r"(?:mon|tue|wed|thu|fri|sat|sun(?:day)?|monday|tuesday|wednesday|thursday|friday|saturday)?\s*"
    r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:\s*,\s*\d{4})?\s*,?\s*)?"
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
    r")", re.IGNORECASE
)


def iter_string_values(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).startswith("__"):
                continue
            yield from iter_string_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_string_values(v)


def _clock_parts(text: str) -> Optional[Tuple[int, int]]:
    m = re.search(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.IGNORECASE)
    if not m:
        return None
    hour = int(m.group(1)); minute = int(m.group(2) or 0); ap = (m.group(3) or "").lower()
    if minute > 59:
        return None
    if ap:
        if not (1 <= hour <= 12):
            return None
        if hour == 12:
            hour = 0
        if ap == "pm":
            hour += 12
    elif not (0 <= hour <= 23):
        return None
    return hour, minute


def parse_reset_spec(spec: str, event_time: Optional[datetime]) -> Optional[datetime]:
    """Parse Claude UI strings such as '10:10pm', '4am', or 'Aug 17, 3am'.

    A time-only reset is resolved in the event timestamp's timezone and to the next
    occurrence after the limit message. That is safe for a five-hour limit because the
    displayed reset must be in the near future.
    """
    if not event_time:
        return None
    # Claude's human-readable reset clock is rendered in the local Claude Code/UI
    # timezone, while transcript timestamps are commonly UTC. Resolve the clock in
    # the machine's local timezone before attaching the date.
    base = event_time.astimezone()
    raw = re.sub(r"\s+", " ", spec.strip().rstrip(".,;)]}"))
    clock = _clock_parts(raw)
    if not clock:
        return None
    hour, minute = clock

    months = {m.lower(): i for i, m in enumerate(
        ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], 1
    )}
    month_match = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
        r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+"
        r"(\d{1,2})(?:\s*,\s*(\d{4}))?", raw, re.IGNORECASE
    )
    if month_match:
        mon_key = month_match.group(1)[:3].lower()
        month = months.get(mon_key)
        day = int(month_match.group(2)); year = int(month_match.group(3) or base.year)
        if not month:
            return None
        try:
            candidate = base.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None
        if month_match.group(3) is None and candidate < base - timedelta(days=180):
            try:
                candidate = candidate.replace(year=year + 1)
            except ValueError:
                pass
        return candidate

    # Optional weekday. Useful if Claude ever renders "resets Mon 12:00am".
    wd_match = re.search(r"\b(mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b", raw, re.IGNORECASE)
    if wd_match:
        names = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
        target = names[wd_match.group(1)[:3].lower()]
        days = (target - base.weekday()) % 7
        candidate = (base + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base:
            candidate += timedelta(days=7)
        return candidate

    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    # A session-limit reset should be nearby. Reject obviously unrelated clock strings.
    if candidate - base > timedelta(hours=6, minutes=15):
        return None
    return candidate


def transcript_limit_events(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract 5h limit/reset evidence from a transcript.

    Textual reset messages are first-class evidence, e.g.
      You've hit your session limit · resets 10:10pm
    Structured five_hour.resets_at remains the strongest transcript evidence.
    """
    events: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        ts = parse_dt(row.get("timestamp"))
        for text in iter_string_values(row):
            if not LIMIT_CONTEXT_RE.search(text):
                continue
            hit = bool(LIMIT_HIT_RE.search(text))
            m = RESET_TEXT_RE.search(text)
            reset = parse_reset_spec(m.group("spec"), ts) if m else None
            kind = "five_hour_limit" if hit else "five_hour_reset_hint"
            confidence = "high" if reset else ("low" if hit else "medium")
            key = (fmt_dt(ts), kind, fmt_dt(reset))
            if key not in seen:
                seen.add(key)
                events.append({
                    "time": ts,
                    "kind": kind,
                    "reset": reset,
                    "confidence": confidence,
                    "source": "transcript-reset-text" if reset else "transcript-limit-text",
                    "raw": text[:500],
                })
        structured: List[Dict[str, Any]] = []
        find_structured_five_hour(row, structured)
        for item in structured:
            reset = parse_dt(item.get("resets_at"))
            if reset:
                key = (fmt_dt(reset), "five_hour_reset")
                if key not in seen:
                    seen.add(key)
                    events.append({
                        "time": ts,
                        "kind": "five_hour_reset",
                        "reset": reset,
                        "used_percentage": item.get("used_percentage"),
                        "confidence": "high",
                        "source": "transcript-structured",
                    })
    return events


def collapse_limit_events(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse repeated blocked attempts into quota-exhaustion incidents.

    Claude Code can append several 429 messages after one five-hour limit is
    reached. Events with the same session and reset timestamp are one incident;
    ``attempts`` preserves how many blocked requests were observed.
    """
    grouped: "collections.OrderedDict[Tuple[str, str], Dict[str, Any]]" = collections.OrderedDict()
    passthrough = 0
    ordered = sorted(
        events,
        key=lambda e: e.get("time") if isinstance(e.get("time"), datetime)
        else datetime.min.replace(tzinfo=timezone.utc),
    )
    for event in ordered:
        if event.get("kind") != "five_hour_limit":
            passthrough += 1
            grouped[("other", str(passthrough))] = {**event, "attempts": 1, "last_time": event.get("time")}
            continue
        reset = event.get("reset")
        session_id = str(event.get("session_id") or "")
        if isinstance(reset, datetime):
            key = (session_id, f"reset:{int(reset.timestamp())}")
        else:
            event_time = event.get("time")
            # Without a reset anchor, group only a small retry burst.
            bucket = int(event_time.timestamp() // 300) if isinstance(event_time, datetime) else passthrough
            key = (session_id, f"unanchored:{bucket}")
        if key not in grouped:
            grouped[key] = {**event, "attempts": 1, "last_time": event.get("time")}
        else:
            incident = grouped[key]
            incident["attempts"] += 1
            t = event.get("time")
            if isinstance(t, datetime):
                last = incident.get("last_time")
                incident["last_time"] = max(last, t) if isinstance(last, datetime) else t
    return list(grouped.values())


def load_statusline_windows(claude_home: Path, session_id: str, main_path: Path) -> List[Dict[str, Any]]:
    log_path = claude_home / "token-profiler-rate-limits.jsonl"
    if not log_path.exists():
        return []
    windows: Dict[int, Dict[str, Any]] = {}
    rows, _ = load_jsonl(log_path)
    for row in rows:
        sid = row.get("session_id")
        tp = row.get("transcript_path")
        if sid and str(sid) != session_id:
            continue
        if tp and Path(str(tp)).name != main_path.name and not sid:
            continue
        reset = parse_dt(row.get("five_hour_resets_at"))
        if not reset:
            continue
        epoch = int(reset.timestamp())
        item = windows.setdefault(epoch, {
            "start": reset - timedelta(hours=5), "end": reset,
            "used_percentage": None, "source": "statusline", "confidence": "high",
        })
        if row.get("five_hour_used_percentage") is not None:
            item["used_percentage"] = row.get("five_hour_used_percentage")
    return [windows[k] for k in sorted(windows)]


def build_five_hour_windows(
    calls: List[ApiCall], rows: List[Dict[str, Any]], claude_home: Path,
    session_id: str, main_path: Path
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    windows = load_statusline_windows(claude_home, session_id, main_path)
    events = transcript_limit_events(rows)
    known = {int(w["end"].timestamp()) for w in windows}
    for e in events:
        reset = e.get("reset")
        if isinstance(reset, datetime):
            epoch = int(reset.timestamp())
            if epoch not in known:
                known.add(epoch)
                windows.append({
                    "start": reset - timedelta(hours=5), "end": reset,
                    "used_percentage": e.get("used_percentage"),
                    "source": e.get("source", "transcript-reset"), "confidence": e.get("confidence", "high"),
                })
    windows.sort(key=lambda w: w["end"])
    for w in windows:
        wcalls = [c for c in calls if call_dt(c) and w["start"] <= call_dt(c).astimezone(w["start"].tzinfo) < w["end"]]
        w["calls"] = len(wcalls)
        w["processed"] = sum(c.context_input for c in wcalls)
        w["cache_read"] = sum(c.cache_read for c in wcalls)
        w["cache_write"] = sum(c.cache_write for c in wcalls)
        w["output"] = sum(c.output_tokens for c in wcalls)
        w["rewrites"] = sum(1 for c in wcalls if is_full_rewrite(c))
    return windows, events




def discover_main_transcripts(claude_home: Path, max_depth: int = 2) -> List[Path]:
    """Find main Claude Code session JSONLs under projects, excluding subagents."""
    projects = claude_home / "projects"
    if not projects.exists():
        return []
    found: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(projects):
        current = Path(dirpath)
        try:
            depth = len(current.relative_to(projects).parts)
        except ValueError:
            continue
        if depth >= max_depth:
            dirnames[:] = []
        if "subagents" in current.parts:
            continue
        for name in filenames:
            if not name.endswith(".jsonl") or name.startswith("agent-"):
                continue
            found.append(current / name)
    return sorted(set(p.resolve() for p in found if p.is_file()))


def collect_global_five_hour_evidence(
    claude_home: Path, search_depth: int, include_subagents: bool = True
) -> Dict[str, Any]:
    """Scan newest transcripts first and collect reset anchors + successful API calls.

    File mtime is used only as scan priority. Period boundaries always come from JSONL
    timestamps / reset text, never from filesystem mtime.
    """
    candidates = discover_main_transcripts(claude_home, search_depth)
    candidates.sort(key=lambda x: x.stat().st_mtime_ns if x.exists() else 0, reverse=True)
    events: List[Dict[str, Any]] = []
    successes: List[Dict[str, Any]] = []
    parse_errors = 0
    for main_path in candidates:
        sid = main_path.stem
        try:
            rows, errs = load_jsonl(main_path)
        except OSError:
            continue
        parse_errors += len(errs)
        for e in transcript_limit_events(rows):
            events.append({**e, "session_id": sid, "path": main_path, "file_mtime_ns": main_path.stat().st_mtime_ns})
        analyses: List[TranscriptAnalysis] = []
        try:
            analyses.append(analyze_transcript(main_path, f"session:{sid[:8]}"))
        except OSError:
            pass
        if include_subagents:
            for sp in find_subagent_files(main_path, sid):
                try:
                    analyses.append(analyze_transcript(sp, f"session:{sid[:8]}/" + sp.stem.replace("agent-", "agent:")))
                except OSError:
                    pass
        for a in analyses:
            for c in a.calls:
                dt = call_dt(c)
                if dt:
                    successes.append({"time": dt, "session_id": sid, "path": main_path, "actor": a.actor})
    events.sort(key=lambda e: e.get("time") or datetime.min.replace(tzinfo=timezone.utc))
    successes.sort(key=lambda x: x["time"])

    # Multiple blocked requests in one cycle often repeat the exact same reset time.
    anchors: Dict[int, Dict[str, Any]] = {}
    for e in events:
        reset = e.get("reset")
        if not isinstance(reset, datetime):
            continue
        epoch = int(reset.timestamp())
        a = anchors.setdefault(epoch, {
            "reset": reset,
            "events": [],
            "sessions": set(),
            "limit_times": [],
            "sources": set(),
            "confidence": "high",
        })
        a["events"].append(e)
        a["sessions"].add(str(e.get("session_id") or ""))
        a["sources"].add(str(e.get("source") or "transcript"))
        if e.get("kind") == "five_hour_limit" and isinstance(e.get("time"), datetime):
            a["limit_times"].append(e["time"])
    anchor_list = []
    for epoch in sorted(anchors):
        a = anchors[epoch]
        a["sessions"] = sorted(x for x in a["sessions"] if x)
        a["sources"] = sorted(a["sources"])
        a["limit_at_first"] = min(a["limit_times"]) if a["limit_times"] else None
        a["limit_at_last"] = max(a["limit_times"]) if a["limit_times"] else None
        anchor_list.append(a)
    return {
        "candidates": candidates,
        "events": events,
        "successes": successes,
        "anchors": anchor_list,
        "parse_errors": parse_errors,
    }


def transcript_explicit_windows(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Each explicit reset timestamp defines a five-hour window ending at that reset."""
    out = []
    for a in evidence.get("anchors", []):
        reset = a["reset"]
        out.append({
            "start": reset - timedelta(hours=5),
            "end": reset,
            "expected_reset": reset,
            "source": "transcript-reset-text",
            "confidence": "high",
            "status": "exhausted" if a.get("limit_at_first") else "reset-observed",
            "limit_at": a.get("limit_at_first"),
            "limit_at_last": a.get("limit_at_last"),
            "sessions": a.get("sessions", []),
        })
    return out


def resolve_current_five_hour_window(
    claude_home: Path, search_depth: int, include_subagents: bool = True,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve the current 5h analysis range without requiring statusLine.

    Priority:
      1) statusLine resets_at (authoritative)
      2) a current blocked transcript message with an explicit reset time
      3) the most recent past reset + current activity
      4) recent successful API activity only (low-confidence fallback)
    """
    now = now or datetime.now().astimezone()
    exact = latest_current_statusline_window(claude_home, now)
    if exact:
        return {**exact, "analysis_end": min(now, exact["end"]), "status": "active-or-unknown", "boundary_method": "statusline"}

    ev = collect_global_five_hour_evidence(claude_home, search_depth, include_subagents)
    anchors = ev["anchors"]
    successes = ev["successes"]

    # If a blocking limit message says it resets in the future, this is the current
    # exhausted window. Prefer a previous explicit reset when it is ~5h earlier.
    future = [a for a in anchors if now < a["reset"] <= now + timedelta(hours=5, minutes=15)
              and a.get("limit_at_first") and a["limit_at_first"] <= now]
    if future:
        a = min(future, key=lambda x: x["reset"])
        reset = a["reset"]
        prevs = [x["reset"] for x in anchors if x["reset"] < reset]
        prev = max(prevs) if prevs else None
        if prev and timedelta(hours=4, minutes=30) <= reset - prev <= timedelta(hours=5, minutes=30):
            start = prev; start_source = "previous-explicit-reset"
        else:
            start = reset - timedelta(hours=5); start_source = "reset-minus-5h"
        return {
            "start": start, "end": reset, "analysis_end": min(now, reset),
            "expected_reset": reset, "source": "transcript-current-limit-reset",
            "confidence": "high", "status": "exhausted",
            "limit_at": a.get("limit_at_first"), "limit_at_last": a.get("limit_at_last"),
            "start_source": start_source, "sessions": a.get("sessions", []),
            "evidence": ev,
        }

    past = [a for a in anchors if a["reset"] <= now]
    latest_reset = max((a["reset"] for a in past), default=None)
    if latest_reset and now - latest_reset <= timedelta(hours=5):
        return {
            "start": latest_reset, "end": now, "analysis_end": now,
            "expected_reset": latest_reset + timedelta(hours=5),
            "source": "transcript-previous-reset-current-active",
            "confidence": "medium", "status": "active",
            "start_source": "latest-explicit-reset", "evidence": ev,
        }

    # No recent reset anchor. Find the latest successful activity and use only the
    # current five-hour activity horizon. This is intentionally low confidence.
    recent_success = [x for x in successes if x["time"] <= now and x["time"] >= now - timedelta(hours=5)]
    if recent_success:
        first = min(x["time"] for x in recent_success)
        return {
            "start": first, "end": now, "analysis_end": now,
            "expected_reset": first + timedelta(hours=5),
            "source": "recent-api-activity-no-reset-anchor",
            "confidence": "low", "status": "active-uncertain",
            "start_source": "first-success-in-last-5h", "evidence": ev,
        }
    return None


def resolve_five_hour_window_at(
    claude_home: Path, when: datetime, search_depth: int, include_subagents: bool = True
) -> Optional[Dict[str, Any]]:
    exact = find_statusline_window_at(claude_home, when)
    if exact:
        return {**exact, "source": "statusline-five-hour-at", "status": "historical", "boundary_method": "statusline"}
    ev = collect_global_five_hour_evidence(claude_home, search_depth, include_subagents)
    windows = [w for w in transcript_explicit_windows(ev) if w["start"] <= when < w["end"]]
    if windows:
        w = min(windows, key=lambda x: x["end"])
        return {**w, "source": "transcript-reset-five-hour-at", "evidence": ev}
    # If the point is shortly after a known reset but no next reset was logged, use
    # that reset as the start of an inferred active window.
    past = [a["reset"] for a in ev["anchors"] if a["reset"] <= when and when - a["reset"] < timedelta(hours=5)]
    if past:
        start = max(past)
        return {
            "start": start, "end": start + timedelta(hours=5), "expected_reset": start + timedelta(hours=5),
            "source": "transcript-previous-reset-five-hour-at", "confidence": "medium",
            "status": "historical-inferred", "evidence": ev,
        }
    return None


def combined_known_five_hour_windows(
    claude_home: Path, search_depth: int, include_subagents: bool = True
) -> List[Dict[str, Any]]:
    by_end: Dict[int, Dict[str, Any]] = {}
    for w in load_all_statusline_windows(claude_home):
        by_end[int(w["end"].timestamp())] = {**w, "source": "statusline", "confidence": "high"}
    ev = collect_global_five_hour_evidence(claude_home, search_depth, include_subagents)
    for w in transcript_explicit_windows(ev):
        epoch = int(w["end"].timestamp())
        by_end.setdefault(epoch, w)
    return [by_end[k] for k in sorted(by_end)]


def parse_cli_datetime(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as e:
        raise ValueError(
            f"Invalid datetime {value!r}; use e.g. '2026-08-13 18:00' or '2026-08-13T18:00:00+08:00'"
        ) from e
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def calls_in_range(calls: Iterable[ApiCall], start: datetime, end: datetime) -> List[ApiCall]:
    out: List[ApiCall] = []
    for c in calls:
        dt = call_dt(c)
        if dt is not None and start <= dt < end:
            out.append(c)
    return out


def period_dynamic_estimate(calls: Iterable[ApiCall]) -> collections.Counter[str]:
    total: collections.Counter[str] = collections.Counter()
    for c in calls:
        total.update(c.preceding_est)
    return total


def numeric(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except Exception:
        return None


def load_all_statusline_windows(claude_home: Path) -> List[Dict[str, Any]]:
    """Load account-level 5h windows captured by --statusline-capture."""
    log_path = claude_home / "token-profiler-rate-limits.jsonl"
    if not log_path.exists():
        return []
    rows, _ = load_jsonl(log_path)
    windows: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        reset = parse_dt(row.get("five_hour_resets_at"))
        if not reset:
            continue
        epoch = int(reset.timestamp())
        w = windows.setdefault(epoch, {
            "start": reset - timedelta(hours=5),
            "end": reset,
            "source": "statusline",
            "confidence": "high",
            "observations": 0,
            "used_percentage_max": None,
            "used_percentage_last": None,
            "first_observed_at": None,
            "last_observed_at": None,
            "sessions": set(),
        })
        observed = parse_dt(row.get("observed_at"))
        used = numeric(row.get("five_hour_used_percentage"))
        w["observations"] += 1
        if used is not None:
            old = w.get("used_percentage_max")
            w["used_percentage_max"] = used if old is None else max(float(old), used)
            if observed is None or w.get("last_observed_at") is None or observed >= w["last_observed_at"]:
                w["used_percentage_last"] = used
        if observed:
            if w["first_observed_at"] is None or observed < w["first_observed_at"]:
                w["first_observed_at"] = observed
            if w["last_observed_at"] is None or observed > w["last_observed_at"]:
                w["last_observed_at"] = observed
        sid = row.get("session_id")
        if sid:
            w["sessions"].add(str(sid))
    result = []
    for k in sorted(windows):
        w = windows[k]
        w["sessions"] = sorted(w["sessions"])
        result.append(w)
    return result


def find_statusline_window_at(claude_home: Path, when: datetime) -> Optional[Dict[str, Any]]:
    matches = [w for w in load_all_statusline_windows(claude_home) if w["start"] <= when < w["end"]]
    if not matches:
        return None
    return max(matches, key=lambda w: w["end"])


def latest_current_statusline_window(claude_home: Path, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    now = now or datetime.now().astimezone()
    return find_statusline_window_at(claude_home, now)


def read_last_json_record(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell() - 1
            buf = bytearray()
            while pos >= 0:
                f.seek(pos)
                b = f.read(1)
                if b == b"\n" and buf:
                    break
                if b != b"\n":
                    buf.extend(b)
                pos -= 1
        line = bytes(reversed(buf)).decode("utf-8", errors="replace").strip()
        obj = json.loads(line) if line else None
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def status_snapshot_signature(record: Dict[str, Any]) -> Tuple[Any, ...]:
    keys = (
        "session_id", "prompt_id", "model", "effort", "thinking_enabled",
        "five_hour_resets_at", "five_hour_used_percentage",
        "seven_day_resets_at", "seven_day_used_percentage",
        "context_used_percentage", "context_total_input_tokens", "context_total_output_tokens",
        "current_input_tokens", "current_cache_read_tokens", "current_cache_write_tokens",
        "current_output_tokens", "session_cost_usd",
    )
    return tuple(record.get(k) for k in keys)


def capture_statusline(claude_home: Path, lang: str) -> int:
    """Capture lightweight subscription/context snapshots from Claude Code statusLine stdin."""
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        print(f"statusline capture error: {e}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        return 2
    matches: List[Dict[str, Any]] = []
    find_structured_five_hour(payload, matches)
    best = matches[-1] if matches else {}
    context = payload.get("context_window") if isinstance(payload.get("context_window"), dict) else {}
    current_usage = context.get("current_usage") if isinstance(context.get("current_usage"), dict) else {}
    cost = payload.get("cost") if isinstance(payload.get("cost"), dict) else {}
    rate_limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
    seven = rate_limits.get("seven_day") if isinstance(rate_limits.get("seven_day"), dict) else {}
    effort = payload.get("effort") if isinstance(payload.get("effort"), dict) else {}
    thinking = payload.get("thinking") if isinstance(payload.get("thinking"), dict) else {}
    model = payload.get("model")
    if isinstance(model, dict):
        model_id = model.get("id") or model.get("display_name")
    else:
        model_id = model
    record: Dict[str, Any] = {
        "observed_at": datetime.now().astimezone().isoformat(),
        "session_id": payload.get("session_id"),
        "session_name": payload.get("session_name"),
        "prompt_id": payload.get("prompt_id"),
        "transcript_path": payload.get("transcript_path"),
        "cwd": payload.get("cwd"),
        "claude_code_version": payload.get("version"),
        "model": model_id,
        "effort": effort.get("level"),
        "thinking_enabled": thinking.get("enabled"),
        "five_hour_resets_at": best.get("resets_at"),
        "five_hour_used_percentage": best.get("used_percentage"),
        "seven_day_resets_at": seven.get("resets_at"),
        "seven_day_used_percentage": seven.get("used_percentage"),
        "context_used_percentage": context.get("used_percentage"),
        "context_window_size": context.get("context_window_size"),
        "context_total_input_tokens": context.get("total_input_tokens"),
        "context_total_output_tokens": context.get("total_output_tokens"),
        "current_input_tokens": current_usage.get("input_tokens"),
        "current_cache_read_tokens": current_usage.get("cache_read_input_tokens"),
        "current_cache_write_tokens": current_usage.get("cache_creation_input_tokens"),
        "current_output_tokens": current_usage.get("output_tokens"),
        "session_cost_usd": cost.get("total_cost_usd"),
    }
    log_path = claude_home / "token-profiler-rate-limits.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    previous = read_last_json_record(log_path)
    if not previous or status_snapshot_signature(previous) != status_snapshot_signature(record):
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    reset = parse_dt(record.get("five_hour_resets_at"))
    used = record.get("five_hour_used_percentage")
    pieces = []
    if used is not None:
        pieces.append(("5小时" if lang == "zh" else "5h") + f" {used}%")
    if reset is not None:
        pieces.append(("重置 " if lang == "zh" else "reset ") + reset.astimezone().strftime("%H:%M"))
    ctx_pct = record.get("context_used_percentage")
    if ctx_pct is not None:
        pieces.append(("上下文 " if lang == "zh" else "ctx ") + f"{ctx_pct}%")
    if pieces:
        print(" · ".join(pieces))
    return 0


def percentile(values: Iterable[int], percentile: float) -> int:
    """Return a linearly interpolated percentile from integer measurements."""
    sorted_values = sorted(values)
    if not sorted_values:
        return 0
    if not 0 <= percentile <= 100:
        raise ValueError("percentile must be between 0 and 100")
    position = (len(sorted_values) - 1) * percentile / 100
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    return int(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower))


def context_distribution(calls: Iterable[ApiCall], thresholds: Iterable[int]) -> Dict[str, Any]:
    """Return context measurements and strict-greater observation buckets."""
    call_list = list(calls)
    contexts = [c.context_input for c in call_list]
    processed = sum(contexts)
    buckets: Dict[str, Dict[str, Any]] = {}
    for threshold in sorted(set(int(value) for value in thresholds)):
        if threshold < 0:
            raise ValueError("context threshold must be non-negative")
        matching = [c for c in call_list if c.context_input > threshold]
        matching_processed = sum(c.context_input for c in matching)
        buckets[f"gt_{threshold}"] = {
            "threshold": threshold,
            "calls": len(matching),
            "call_pct": 100.0 * len(matching) / len(call_list) if call_list else 0.0,
            "processed": matching_processed,
            "processed_pct": 100.0 * matching_processed / processed if processed else 0.0,
        }
    return {
        "percentiles": {f"p{p}": percentile(contexts, p) for p in (50, 75, 90, 95)},
        "max": max(contexts, default=0),
        "buckets": buckets,
    }


def diagnosis_metrics(
    all_calls: List[ApiCall], est_total: collections.Counter[str], limit_events: List[Dict[str, Any]],
    context_threshold: Optional[int] = None,
) -> Dict[str, Any]:
    rewrites = [c for c in all_calls if is_full_rewrite(c)]
    rewrite_tokens = sum(c.cache_write for c in rewrites)
    cache_write_total = sum(c.cache_write for c in all_calls)
    processed = sum(c.context_input for c in all_calls)
    thinking = int(est_total.get("assistant:thinking", 0))
    thresholds = (context_threshold,) if context_threshold is not None else DEFAULT_CONTEXT_THRESHOLDS
    return {
        "context_distribution": context_distribution(all_calls, thresholds),
        "peak_context": max((c.context_input for c in all_calls), default=0),
        "processed": processed,
        "api_calls": len(all_calls),
        "full_rewrite_count": len(rewrites),
        "full_rewrite_tokens": rewrite_tokens,
        "full_rewrite_cache_write_pct": (100.0 * rewrite_tokens / cache_write_total) if cache_write_total else 0.0,
        "thinking_estimate": thinking,
        "limit_events": len([e for e in limit_events if e.get("kind") == "five_hour_limit"]),
    }


def build_period_diagnosis(
    start: datetime, end: datetime, status: str, limit_at: Optional[datetime],
    sessions: List[Dict[str, Any]], calls: List[ApiCall], metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the compact, decision-oriented summary shown before period details."""
    responsible = max(sessions, key=lambda s: int(s.get("processed", 0)), default={})
    total_processed = sum(int(s.get("processed", 0)) for s in sessions)
    responsible_processed = int(responsible.get("processed", 0))
    rewrites = [c for c in calls if is_full_rewrite(c) and call_dt(c)]
    latest_rewrite = max(rewrites, key=lambda c: call_dt(c), default=None)
    rewrite_at = call_dt(latest_rewrite) if latest_rewrite else None
    proximity = None
    if limit_at and rewrite_at and rewrite_at <= limit_at:
        proximity = int((limit_at - rewrite_at).total_seconds())
    return {
        "status": status,
        "start": start,
        "end": end,
        "limit_at": limit_at,
        "limit_elapsed_seconds": int((limit_at - start).total_seconds()) if limit_at else None,
        "responsible_session_id": responsible.get("session_id"),
        "responsible_processed_pct": (
            100.0 * responsible_processed / total_processed if total_processed else 0.0
        ),
        "responsible_thinking": int(responsible.get("thinking", 0)),
        "latest_rewrite_at": rewrite_at,
        "latest_rewrite_context": latest_rewrite.context_input if latest_rewrite else 0,
        "latest_rewrite_seconds_before_limit": proximity,
        **metrics,
    }


def compact_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "-"
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}h{minutes}m{secs}s"


def print_period_diagnosis(summary: Dict[str, Any], lang: str) -> None:
    """Print the period's actionable diagnosis before supporting details."""
    if lang != "zh":
        print("[PERIOD DIAGNOSIS]")
        print_rule()
        print(f"Result: {summary.get('status', 'unknown')}")
        return

    start = summary.get("start")
    end = summary.get("end")
    limit_at = summary.get("limit_at")
    sid = str(summary.get("responsible_session_id") or "-")[:8]
    status_text = "5小时额度已耗尽" if summary.get("status") == "exhausted" else "周期尚未确认耗尽"
    print("[周期诊断]")
    print_rule()
    print(f"结果：{status_text}")
    if isinstance(start, datetime) and isinstance(end, datetime):
        print(f"周期：{start.astimezone().strftime('%H:%M')} → {end.astimezone().strftime('%H:%M')}")
    print(f"耗尽：{limit_at.astimezone().strftime('%H:%M:%S') if isinstance(limit_at, datetime) else '-'}")
    print(f"耗尽耗时：{compact_duration(summary.get('limit_elapsed_seconds'))}")
    print("\n主要责任会话：")
    print(sid)
    print(f"贡献 {summary.get('responsible_processed_pct', 0):.1f}% 的处理输入")
    print("\n最大风险：")
    print("① 长上下文")
    print(f"   峰值 {human_tokens(summary.get('peak_context', 0))}")
    distribution = summary.get("context_distribution", {})
    percentiles = distribution.get("percentiles", {})
    print("   P50/P75/P90/P95 " + "/".join(
        human_tokens(percentiles.get(key, 0)) for key in ("p50", "p75", "p90", "p95")
    ))
    print("\n② Prompt Cache整段重写")
    print(f"   {summary.get('full_rewrite_count', 0)}次，共{human_tokens(summary.get('full_rewrite_tokens', 0))}")
    print(f"   占cache write {summary.get('full_rewrite_cache_write_pct', 0):.1f}%")
    print("\n③ 最近一次大重写")
    rw_at = summary.get("latest_rewrite_at")
    rw_time = rw_at.astimezone().strftime("%H:%M:%S") if isinstance(rw_at, datetime) else "-"
    print(f"   {rw_time} {human_tokens(summary.get('latest_rewrite_context', 0))}")
    proximity = summary.get("latest_rewrite_seconds_before_limit")
    print(f"   距额度耗尽仅{proximity}秒" if proximity is not None else "   与额度耗尽的时间关系未知")
    print("\n④ Thinking膨胀")
    print(f"   本周期 ~{human_tokens(summary.get('thinking_estimate', 0))}")
    print(f"   其中{sid} ~{human_tokens(summary.get('responsible_thinking', 0))}")
    print("\n建议：")
    print(f"优先 compact/clear {sid}；")
    print("避免500k+ session长时间挂起后继续resume；")
    print("普通任务减少Opus高effort使用。")


def _legacy_diagnosis_reference(metrics: Dict[str, Any], lang: str) -> None:
    zh = lang == "zh"
    print("\n[诊断摘要]" if zh else "\n[DIAGNOSTIC SUMMARY]")
    print_rule()
    if zh:
        print(f"严重程度                 : {metrics['severity']}")
        print(f"峰值单次输入上下文         : {human_tokens(metrics['peak_context'])}")
        print(f">150k 上下文调用           : {metrics['long_context_calls']}/{metrics['api_calls']} ({metrics['long_context_call_pct']:.1f}%)")
        print(f">150k 调用贡献的处理输入    : {human_tokens(metrics['long_context_processed'])} ({metrics['long_context_processed_pct']:.1f}%)")
        print(f"FULL_REWRITE             : {metrics['full_rewrite_count']} 次 / {human_tokens(metrics['full_rewrite_tokens'])}")
        print(f"FULL_REWRITE 占缓存写入    : {metrics['full_rewrite_cache_write_pct']:.1f}%")
        print(f"Claude 思考内容估算         : ~{human_tokens(metrics['thinking_estimate'])}")
        print(f"检测到 5 小时额度事件       : {metrics['limit_events']} 次")
        factors = []
        if metrics['peak_context'] > 150_000: factors.append("超长上下文反复进入模型")
        if metrics['full_rewrite_count']: factors.append("大段 Prompt Cache 未命中/重写")
        if metrics['thinking_estimate'] > 50_000: factors.append("Thinking 持续扩大会话历史")
        if metrics['api_calls'] >= 30: factors.append("模型/API 调用次数较多")
        print("主要消耗因素             : " + ("；".join(factors) if factors else "未发现明显异常"))
    else:
        print(f"Severity                  : {metrics['severity']}")
        print(f"Peak request context      : {human_tokens(metrics['peak_context'])}")
        print(f">150k context calls       : {metrics['long_context_calls']}/{metrics['api_calls']} ({metrics['long_context_call_pct']:.1f}%)")
        print(f">150k processed share     : {human_tokens(metrics['long_context_processed'])} ({metrics['long_context_processed_pct']:.1f}%)")
        print(f"FULL_REWRITE              : {metrics['full_rewrite_count']} / {human_tokens(metrics['full_rewrite_tokens'])}")
        print(f"Rewrite share of cacheW   : {metrics['full_rewrite_cache_write_pct']:.1f}%")
        print(f"Thinking estimate         : ~{human_tokens(metrics['thinking_estimate'])}")
        print(f"5h limit events           : {metrics['limit_events']}")


def print_diagnosis(metrics: Dict[str, Any], lang: str) -> None:
    """Print measurements without assigning a risk severity."""
    distribution = metrics["context_distribution"]
    percentiles = distribution["percentiles"]
    print("\n[上下文分布]" if lang == "zh" else "\n[CONTEXT DISTRIBUTION]")
    print_rule()
    print("P50/P75/P90/P95           : " + "/".join(
        human_tokens(percentiles[key]) for key in ("p50", "p75", "p90", "p95")
    ))
    print(f"Maximum request context   : {human_tokens(distribution['max'])}")
    for bucket in distribution["buckets"].values():
        print(
            f">{human_tokens(bucket['threshold'])} context          : "
            f"{bucket['calls']}/{metrics['api_calls']} ({bucket['call_pct']:.1f}% calls), "
            f"{human_tokens(bucket['processed'])} ({bucket['processed_pct']:.1f}% processed)"
        )
    print(f"Large cache creations     : {metrics['full_rewrite_count']} / {human_tokens(metrics['full_rewrite_tokens'])} ({metrics['full_rewrite_cache_write_pct']:.1f}% cache write)")
    print(f"Thinking estimate         : ~{human_tokens(metrics['thinking_estimate'])}")
    print(f"5h limit events           : {metrics['limit_events']}")


def infer_limit_reset_brackets(calls: List[ApiCall], events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bracket reset time using: limit error occurred, then next successful API response.

    This never claims an exact 5h boundary. It is useful for historical sessions where
    statusLine reset timestamps were not captured.
    """
    success_times = sorted(dt for c in calls if (dt := call_dt(c)) is not None)
    limit_times = sorted(e["time"] for e in events if e.get("kind") == "five_hour_limit" and isinstance(e.get("time"), datetime))
    grouped: Dict[datetime, List[datetime]] = {}
    for lt in limit_times:
        nxt = next((t for t in success_times if t > lt), None)
        if nxt is not None:
            grouped.setdefault(nxt, []).append(lt)
    out = []
    for nxt, lts in sorted(grouped.items(), key=lambda kv: kv[0]):
        last_limit = max(lts)
        first_limit = min(lts)
        gap = nxt - last_limit
        conf = "medium" if gap <= timedelta(hours=2) else "low"
        out.append({
            "limit_first": first_limit,
            "limit_last": last_limit,
            "next_success": nxt,
            "reset_after": last_limit,
            "reset_before_or_at": nxt,
            "window_start_after": last_limit - timedelta(hours=5),
            "window_start_before_or_at": nxt - timedelta(hours=5),
            "confidence": conf,
        })
    return out


def report_analysis(
    analyses: List[TranscriptAnalysis], context_info: Optional[Dict[str, Any]], top_n: int,
    lang: str, claude_home: Path, session_id: str, main_path: Path, main_rows: List[Dict[str, Any]],
    context_thresholds: Iterable[int] = DEFAULT_CONTEXT_THRESHOLDS,
) -> Dict[str, Any]:
    zh = lang == "zh"
    all_calls = [c for a in analyses for c in a.calls]
    totals = {
        "unique_api_calls": len(all_calls),
        "input_tokens": sum(c.input_tokens for c in all_calls),
        "cache_read_input_tokens": sum(c.cache_read for c in all_calls),
        "cache_creation_input_tokens": sum(c.cache_write for c in all_calls),
        "output_tokens_jsonl_max_snapshot": sum(c.output_tokens for c in all_calls),
        "cache_creation_5m_tokens": sum(c.cache_write_5m for c in all_calls),
        "cache_creation_1h_tokens": sum(c.cache_write_1h for c in all_calls),
        "total_context_input_processed": sum(c.context_input for c in all_calls),
    }
    est_total: collections.Counter[str] = collections.Counter()
    for a in analyses:
        est_total.update(a.estimated_categories_total)
    limit_events = transcript_limit_events(main_rows)
    diag = diagnosis_metrics(all_calls, est_total, limit_events)
    diag["context_distribution"] = context_distribution(all_calls, context_thresholds)
    print_diagnosis(diag, lang)

    dts = [call_dt(c) for c in all_calls if call_dt(c)]
    start_dt = min(dts) if dts else None
    end_dt = max(dts) if dts else None
    print("\n[会话时间范围]" if zh else "\n[SESSION TIME RANGE]")
    print_rule()
    print(("开始" if zh else "Start") + f" : {fmt_dt(start_dt)}")
    print(("结束" if zh else "End") + f" : {fmt_dt(end_dt)}")
    if start_dt and end_dt:
        print(("跨度" if zh else "Span") + f" : {duration_text((end_dt-start_dt).total_seconds(), lang)}")

    print("\n[JSONL 去重后的 Token 汇总]" if zh else "\n[JSONL DEDUPED TOKEN TOTALS]")
    print_rule()
    fields = [
        ("唯一 API 调用" if zh else "Unique API calls", totals["unique_api_calls"], False),
        ("普通输入 Token" if zh else "Input tokens", totals["input_tokens"], True),
        ("缓存读取" if zh else "Cache read", totals["cache_read_input_tokens"], True),
        ("缓存写入" if zh else "Cache write", totals["cache_creation_input_tokens"], True),
    ]
    for k, v, human in fields:
        print(f"{k:<24}: {human_tokens(v) if human else v}")
    if totals["cache_creation_5m_tokens"] or totals["cache_creation_1h_tokens"]:
        print(f"{'缓存写入 5 分钟' if zh else 'cache write 5m':<24}: {human_tokens(totals['cache_creation_5m_tokens'])}")
        print(f"{'缓存写入 1 小时' if zh else 'cache write 1h':<24}: {human_tokens(totals['cache_creation_1h_tokens'])}")
    print(f"{'输出（JSONL 最佳快照）' if zh else 'Output (best JSONL snapshot)':<24}: {human_tokens(totals['output_tokens_jsonl_max_snapshot'])}")
    print(f"{'累计处理的上下文输入' if zh else 'Context input processed':<24}: {human_tokens(totals['total_context_input_processed'])}")
    for a in analyses:
        print(f"\n  {actor_label(a.actor, lang)}: {a.path}")
        print((f"    唯一调用={len(a.calls)}, 解析错误={len(a.parse_errors)}" if zh else f"    unique calls={len(a.calls)}, parse errors={len(a.parse_errors)}"))

    print("\n[按输入上下文排序的最大 API 调用]" if zh else "\n[TOP API CALLS BY INPUT CONTEXT]")
    print_rule()
    if zh:
        print(f"{'#':>3}  {'时间':<17} {'执行体':<12} {'上下文':>9} {'缓存读':>9} {'缓存写':>9} {'输出':>8}  {'模型':<22} {'请求'}")
    else:
        print(f"{'#':>3}  {'time':<17} {'actor':<12} {'context':>9} {'cacheR':>9} {'cacheW':>9} {'output':>8}  {'model':<22} {'request'}")
    for i, c in enumerate(sorted(all_calls, key=lambda c: c.context_input, reverse=True)[:top_n], 1):
        rid = c.request_id[-12:] if len(c.request_id) > 12 else c.request_id
        print(f"{i:>3}  {fmt_short_dt(call_dt(c)):<17} {actor_label(c.actor, lang):<12.12} {human_tokens(c.context_input):>9} {human_tokens(c.cache_read):>9} {human_tokens(c.cache_write):>9} {human_tokens(c.output_tokens):>8}  {c.model:<22.22} {rid}")

    rewrites = [c for c in all_calls if is_full_rewrite(c)]
    print("\n[缓存失效 / 大段重写事件]" if zh else "\n[CACHE MISS / LARGE REWRITE EVENTS]")
    print_rule()
    if not rewrites:
        print("未检测到明显的大段缓存重写。" if zh else "No obvious large cache rewrites detected.")
    else:
        if zh:
            print(f"{'#':>3}  {'时间':<17} {'执行体':<12} {'上下文':>9} {'缓存读':>9} {'缓存写':>9} {'重写占比':>9}  类型")
        else:
            print(f"{'#':>3}  {'time':<17} {'actor':<12} {'context':>9} {'cacheR':>9} {'cacheW':>9} {'rewrite%':>9}  type")
        for i, c in enumerate(sorted(rewrites, key=lambda x: x.cache_write, reverse=True)[:top_n], 1):
            pct = 100*c.cache_write/max(1,c.context_input)
            print(f"{i:>3}  {fmt_short_dt(call_dt(c)):<17} {actor_label(c.actor, lang):<12.12} {human_tokens(c.context_input):>9} {human_tokens(c.cache_read):>9} {human_tokens(c.cache_write):>9} {pct:>8.1f}%  FULL_REWRITE")

    growth_rows: List[Tuple[int, ApiCall, int, str, int]] = []
    for a in analyses:
        ordered = sorted(a.calls, key=lambda c: call_dt(c) or datetime.min.replace(tzinfo=timezone.utc))
        prev: Optional[ApiCall] = None
        for c in ordered:
            if prev is not None and prev.context_input > 0 and c.context_input > 0 and not is_full_rewrite(c):
                delta = c.context_input - prev.context_input
                est = sum(c.preceding_est.values())
                if delta > 0 and est >= max(1000, int(delta * 0.20)):
                    source = ", ".join(f"{dynamic_label(k, lang)}~{human_tokens(v)}" for k,v in c.preceding_est.most_common(4)) or "-"
                    growth_rows.append((delta, c, prev.context_input, source, est))
            prev = c
    print("\n[可信的上下文增长热点]" if zh else "\n[CREDIBLE CONTEXT GROWTH HOTSPOTS]")
    print_rule()
    if zh:
        print("仅保留可由前置 transcript 内容解释的增长；缓存整段重写不会计入这里。")
        print(f"{'#':>3}  {'时间':<17} {'执行体':<12} {'增长':>9} {'新上下文':>9}  可能新增内容")
    else:
        print("Only growth supported by preceding transcript content is shown; full cache rewrites are excluded.")
        print(f"{'#':>3}  {'time':<17} {'actor':<12} {'delta':>9} {'new ctx':>9}  likely new content")
    for i, (delta, c, _prev, source, _est) in enumerate(sorted(growth_rows,key=lambda x:x[0],reverse=True)[:top_n],1):
        print(f"{i:>3}  {fmt_short_dt(call_dt(c)):<17} {actor_label(c.actor, lang):<12.12} {human_tokens(delta):>9} {human_tokens(c.context_input):>9}  {source}")

    print("\n[动态会话内容估算]" if zh else "\n[DYNAMIC TRANSCRIPT CONTENT ESTIMATE]")
    print_rule()
    print("用于定位超大的工具结果/思考内容；这是启发式估算，不是计费 Token。" if zh else "Heuristic estimate for oversized tool results/thinking; not billable token counts.")
    for name,val in est_total.most_common(25):
        print(f"{dynamic_label(name,lang):<58} ~{human_tokens(val):>9}")

    if context_info and context_info.get("categories"):
        print("\n[当前 /context 基线]" if zh else "\n[CURRENT /CONTEXT BASELINE]")
        print_rule()
        name_map = {"System prompt":"系统提示词","System tools":"系统工具","Memory files":"Memory 文件","Skills":"Skills","Messages":"Messages"}
        for k in ("System prompt","System tools","Memory files","Skills","Messages"):
            if k in context_info["categories"]:
                print(f"{(name_map.get(k,k) if zh else k):<20}: {human_tokens(context_info['categories'][k]):>9}")

    windows, _ = build_five_hour_windows(all_calls, main_rows, claude_home, session_id, main_path)
    print("\n[订阅 5 小时窗口]" if zh else "\n[SUBSCRIPTION 5-HOUR WINDOWS]")
    print_rule()
    if not windows:
        print("未发现精确的 5 小时窗口边界。只有 statusLine/结构化 resets_at 才会被标记为精确窗口。" if zh else "No exact 5-hour boundary found. Only statusLine/structured resets_at is treated as exact.")
    else:
        if zh:
            print(f"{'时间范围':<43} {'调用':>6} {'处理输入':>10} {'缓存读':>9} {'缓存写':>9} {'输出':>8} {'重写':>6} {'账户%':>8}  来源/置信度")
        else:
            print(f"{'range':<43} {'calls':>6} {'processed':>10} {'cacheR':>9} {'cacheW':>9} {'output':>8} {'rewrite':>7} {'acct%':>8}  source/confidence")
        for w in windows:
            rng=f"{fmt_dt(w['start'])} -> {fmt_dt(w['end'])}"
            pct=w.get('used_percentage'); pct_s='-' if pct is None else f"{pct}%"
            print(f"{rng:<43} {w.get('calls',0):>6} {human_tokens(w.get('processed',0)):>10} {human_tokens(w.get('cache_read',0)):>9} {human_tokens(w.get('cache_write',0)):>9} {human_tokens(w.get('output',0)):>8} {w.get('rewrites',0):>6} {pct_s:>8}  {w.get('source')}/{w.get('confidence')}")

    if limit_events:
        print("\n[检测到的限额事件]" if zh else "\n[DETECTED LIMIT EVENTS]")
        for e in limit_events:
            reset = e.get("reset")
            print(f"- {fmt_dt(e.get('time'))}  {e.get('kind')}  reset={fmt_dt(reset) if reset else '-'}  confidence={e.get('confidence')}")
    inferred = infer_limit_reset_brackets(all_calls, limit_events)
    if inferred:
        print("\n[历史 5 小时边界推断（非精确）]" if zh else "\n[HISTORICAL 5H BOUNDARY INFERENCE (NOT EXACT)]")
        print_rule()
        if zh:
            print("依据：出现额度打满提示后，下一条带 usage 的 API 响应说明额度已在两者之间恢复。")
            print(f"{'打满时间':<25} {'下一次成功调用':<25} {'重置发生区间':<53} {'置信度':>8}")
        else:
            print("Basis: after a limit error, the next API response with usage proves reset happened in between.")
        for x in inferred:
            bracket=f"({fmt_dt(x['reset_after'])}, {fmt_dt(x['reset_before_or_at'])}]"
            if zh:
                print(f"{fmt_dt(x['limit_last']):<25} {fmt_dt(x['next_success']):<25} {bracket:<53} {x['confidence']:>8}")
            else:
                print(f"limit={fmt_dt(x['limit_last'])} next_success={fmt_dt(x['next_success'])} reset_in={bracket} confidence={x['confidence']}")

    report = {
        "profiler_version": __version__, "language": lang, "totals": totals, "diagnosis": diag,
        "context": context_info, "session_time_range": {"start": fmt_dt(start_dt), "end": fmt_dt(end_dt)},
        "five_hour_windows": [{**w, "start": fmt_dt(w['start']), "end": fmt_dt(w['end'])} for w in windows],
        "limit_events": [{**e, "time": fmt_dt(e.get('time')), "reset": fmt_dt(e.get('reset')) if e.get('reset') else None} for e in limit_events],
        "inferred_reset_brackets": [{k:(fmt_dt(v) if isinstance(v,datetime) else v) for k,v in x.items()} for x in inferred],
        "transcripts": [],
    }
    for a in analyses:
        report["transcripts"].append({
            "actor": a.actor, "path": str(a.path), "parse_errors": a.parse_errors,
            "estimated_dynamic_categories": dict(a.estimated_categories_total),
            "calls": [{
                "request_id": c.request_id, "line": c.line, "timestamp": c.timestamp, "model": c.model,
                "input_tokens": c.input_tokens, "cache_read_input_tokens": c.cache_read,
                "cache_creation_input_tokens": c.cache_write, "output_tokens_jsonl_snapshot": c.output_tokens,
                "cache_creation_5m_tokens": c.cache_write_5m, "cache_creation_1h_tokens": c.cache_write_1h,
                "context_input_tokens": c.context_input, "full_cache_rewrite": is_full_rewrite(c),
                "preceding_transcript_estimate": dict(c.preceding_est),
            } for c in a.calls],
        })
    return report


def print_known_windows(claude_home: Path, lang: str, search_depth: int = 2, include_subagents: bool = True) -> int:
    zh = lang == "zh"
    windows = combined_known_five_hour_windows(claude_home, search_depth, include_subagents)
    print(f"{'Profiler 版本' if zh else 'Profiler version'} : {__version__}")
    print("\n[可识别的 5 小时窗口]" if zh else "\n[KNOWN 5-HOUR WINDOWS]")
    print_rule()
    if not windows:
        print("未从 statusLine 或历史 JSONL 中找到可靠 reset 锚点。" if zh else "No reliable reset anchors found in statusLine or transcripts.")
        return 0
    if zh:
        print(f"{'时间范围':<53} {'来源':<28} {'置信度':<8} {'耗尽时间':<24} {'账户最大%':>10}")
    else:
        print(f"{'range':<53} {'source':<28} {'confidence':<10} {'limit_at':<24} {'acct max%':>10}")
    for w in windows:
        rng=f"{fmt_dt(w['start'])} -> {fmt_dt(w['end'])}"
        mx=w.get('used_percentage_max')
        mxs='-' if mx is None else f"{float(mx):.1f}%"
        lim=fmt_dt(w.get('limit_at'))
        print(f"{rng:<53} {str(w.get('source','-')):<28.28} {str(w.get('confidence','-')):<10.10} {lim:<24} {mxs:>10}")
    return 0


def analyze_period(
    claude_home: Path, start: datetime, end: datetime, search_depth: int,
    top_n: int, lang: str, include_subagents: bool, source: str,
    exact_window: Optional[Dict[str, Any]] = None,
    context_thresholds: Iterable[int] = DEFAULT_CONTEXT_THRESHOLDS,
) -> Dict[str, Any]:
    zh = lang == "zh"
    session_rows: List[Dict[str, Any]] = []
    all_period_calls: List[ApiCall] = []
    aggregate_dynamic: collections.Counter[str] = collections.Counter()
    models_processed: collections.Counter[str] = collections.Counter()
    parse_errors_total = 0
    candidates = discover_main_transcripts(claude_home, search_depth)
    # Scan every discovered transcript. File mtime is not a reliable content-range
    # boundary after restores, copies, or later metadata writes.
    scan_candidates = candidates

    for main_path in scan_candidates:
        sid = main_path.stem
        try:
            main_rows, main_errors = load_jsonl(main_path)
            main_a = analyze_transcript(main_path, f"session:{sid[:8]}")
        except OSError:
            continue
        analyses = [main_a]
        if include_subagents:
            for sp in find_subagent_files(main_path, sid):
                try:
                    analyses.append(analyze_transcript(sp, f"session:{sid[:8]}/" + sp.stem.replace("agent-", "agent:")))
                except OSError:
                    pass
        period_calls: List[ApiCall] = []
        for a in analyses:
            selected = calls_in_range(a.calls, start, end)
            period_calls.extend(selected)
        if not period_calls:
            continue
        cwd = detect_cwd(main_rows, main_path.parent)
        dyn = period_dynamic_estimate(period_calls)
        turns = aggregate_conversation_turns(main_rows, period_calls, start, end)
        aggregate_dynamic.update(dyn)
        all_period_calls.extend(period_calls)
        for c in period_calls:
            models_processed[c.model] += c.context_input
        rewrites = [c for c in period_calls if is_full_rewrite(c)]
        dts = [call_dt(c) for c in period_calls if call_dt(c)]
        session_rows.append({
            "session_id": sid,
            "path": main_path,
            "cwd": cwd,
            "calls": len(period_calls),
            "processed": sum(c.context_input for c in period_calls),
            "cache_read": sum(c.cache_read for c in period_calls),
            "cache_write": sum(c.cache_write for c in period_calls),
            "output": sum(c.output_tokens for c in period_calls),
            "rewrites": len(rewrites),
            "rewrite_tokens": sum(c.cache_write for c in rewrites),
            "peak_context": max((c.context_input for c in period_calls), default=0),
            "thinking": int(dyn.get("assistant:thinking",0)),
            "turns": turns,
            "first": min(dts) if dts else None,
            "last": max(dts) if dts else None,
            "parse_errors": len(main_errors) + sum(len(a.parse_errors) for a in analyses[1:]),
        })
        parse_errors_total += session_rows[-1]["parse_errors"]

    limit_events_all: List[Dict[str, Any]] = []
    for main_path in scan_candidates:
        try:
            rows, _ = load_jsonl(main_path)
        except OSError:
            continue
        for e in transcript_limit_events(rows):
            t=e.get("time")
            if isinstance(t,datetime) and start <= t < end:
                limit_events_all.append({**e,"session_id":main_path.stem})

    limit_incidents = collapse_limit_events(limit_events_all)
    diag = diagnosis_metrics(all_period_calls, aggregate_dynamic, limit_incidents)
    diag["context_distribution"] = context_distribution(all_period_calls, context_thresholds)
    detected_limit_at = min(
        (e["time"] for e in limit_incidents
         if e.get("kind") == "five_hour_limit" and isinstance(e.get("time"), datetime)),
        default=None,
    )
    period_summary = build_period_diagnosis(
        start=start,
        end=end,
        status=str(exact_window.get("status", "unknown")) if exact_window else ("exhausted" if detected_limit_at else "unknown"),
        limit_at=(exact_window.get("limit_at") if exact_window else None) or detected_limit_at,
        sessions=session_rows,
        calls=all_period_calls,
        metrics=diag,
    )
    print_period_diagnosis(period_summary, lang)
    print()
    print(f"{'Profiler 版本' if zh else 'Profiler version'} : {__version__}")
    print(f"{'分析模式' if zh else 'Mode'}       : {'周期聚合' if zh else 'period aggregate'}")
    print("\n[周期时间范围]" if zh else "\n[PERIOD TIME RANGE]")
    print_rule()
    print(("开始" if zh else "Start") + f" : {fmt_dt(start)}")
    print(("结束" if zh else "End") + f" : {fmt_dt(end)}")
    print(("跨度" if zh else "Span") + f" : {duration_text((end-start).total_seconds(),lang)}")
    print(("边界来源" if zh else "Boundary source") + f" : {source}")
    print(("发现主会话文件" if zh else "Main transcripts found") + f" : {len(candidates)}")
    print(("实际扫描主会话文件" if zh else "Main transcripts scanned") + f" : {len(scan_candidates)}")
    if exact_window:
        mx=exact_window.get("used_percentage_max")
        last=exact_window.get("used_percentage_last")
        conf=exact_window.get("confidence", "high" if str(exact_window.get("source","")).startswith("statusline") else "medium")
        status=exact_window.get("status", "unknown")
        expected=exact_window.get("expected_reset") or (exact_window.get("end") if status != "active" else None)
        limit_at=exact_window.get("limit_at")
        if zh:
            print(f"边界置信度               : {conf}")
            print(f"周期状态                 : {status}")
            if exact_window.get("start_source"): print(f"周期开始依据             : {exact_window.get('start_source')}")
            if expected: print(f"预计/明确重置时间         : {fmt_dt(expected)}")
            if limit_at:
                print(f"检测到额度耗尽时间         : {fmt_dt(limit_at)}")
                if isinstance(exact_window.get("start"), datetime):
                    print(f"从周期开始到耗尽           : {duration_text((limit_at-exact_window['start']).total_seconds(),lang)}")
            if mx is not None: print(f"账户 5h 最大已用          : {mx:.1f}%")
            if last is not None: print(f"账户 5h 最后快照          : {last:.1f}%")
            if exact_window.get('observations') is not None: print(f"statusLine 快照数         : {exact_window.get('observations',0)}")
        else:
            print(f"Boundary confidence      : {conf}")
            print(f"Window status            : {status}")
            if exact_window.get("start_source"): print(f"Window start basis       : {exact_window.get('start_source')}")
            if expected: print(f"Expected/known reset     : {fmt_dt(expected)}")
            if limit_at:
                print(f"Limit reached at         : {fmt_dt(limit_at)}")
                if isinstance(exact_window.get("start"), datetime):
                    print(f"Time to exhaustion       : {duration_text((limit_at-exact_window['start']).total_seconds(),lang)}")
            if mx is not None: print(f"Account 5h max used      : {mx:.1f}%")
            if last is not None: print(f"Account 5h last snapshot : {last:.1f}%")

    print_diagnosis(diag, lang)
    print("\n[周期 Token 汇总（本机 JSONL）]" if zh else "\n[PERIOD TOKEN TOTALS (LOCAL JSONL)]")
    print_rule()
    totals = {
        "sessions": len(session_rows),
        "calls": len(all_period_calls),
        "input": sum(c.input_tokens for c in all_period_calls),
        "cache_read": sum(c.cache_read for c in all_period_calls),
        "cache_write": sum(c.cache_write for c in all_period_calls),
        "output": sum(c.output_tokens for c in all_period_calls),
        "processed": sum(c.context_input for c in all_period_calls),
        "rewrite_tokens": sum(c.cache_write for c in all_period_calls if is_full_rewrite(c)),
        "rewrites": sum(1 for c in all_period_calls if is_full_rewrite(c)),
    }
    if zh:
        print(f"有活动的会话             : {totals['sessions']}")
        print(f"唯一 API 调用            : {totals['calls']}")
        print(f"累计处理上下文输入        : {human_tokens(totals['processed'])}")
        print(f"缓存读取                 : {human_tokens(totals['cache_read'])}")
        print(f"缓存写入                 : {human_tokens(totals['cache_write'])}")
        print(f"输出                     : {human_tokens(totals['output'])}")
        print(f"FULL_REWRITE            : {totals['rewrites']} 次 / {human_tokens(totals['rewrite_tokens'])}")
        print("说明                     : 仅聚合本机 projects JSONL；Claude.ai、其他设备的消耗不会出现在 Token 汇总中。")
    else:
        print(f"Active sessions          : {totals['sessions']}")
        print(f"Unique API calls         : {totals['calls']}")
        print(f"Context input processed  : {human_tokens(totals['processed'])}")
        print(f"Cache read               : {human_tokens(totals['cache_read'])}")
        print(f"Cache write              : {human_tokens(totals['cache_write'])}")
        print(f"Output                    : {human_tokens(totals['output'])}")

    print("\n[会话贡献排名]" if zh else "\n[SESSION CONTRIBUTION RANKING]")
    print_rule()
    if zh:
        print(f"{'#':>3} {'会话ID':<14} {'项目':<20} {'活动范围':<29} {'调用':>5} {'处理输入':>10} {'缓存读':>9} {'缓存写':>9} {'输出':>8} {'重写':>5} {'峰值ctx':>9} {'思考~':>9}")
    for i,s in enumerate(sorted(session_rows,key=lambda x:x['processed'],reverse=True)[:top_n],1):
        sid=s['session_id'][:12]
        project=Path(s['cwd']).name or str(s['cwd'])
        if zh:
            active=f"{fmt_short_dt(s['first'])}->{fmt_short_dt(s['last'])}"
            print(f"{i:>3} {sid:<14} {project[:20]:<20} {active:<29} {s['calls']:>5} {human_tokens(s['processed']):>10} {human_tokens(s['cache_read']):>9} {human_tokens(s['cache_write']):>9} {human_tokens(s['output']):>8} {s['rewrites']:>5} {human_tokens(s['peak_context']):>9} {human_tokens(s['thinking']):>9}")
        else:
            print(f"{i:>3} {sid:<14} {project[:24]:<24} calls={s['calls']:>4} processed={human_tokens(s['processed']):>9} cacheR={human_tokens(s['cache_read']):>9} cacheW={human_tokens(s['cache_write']):>9} out={human_tokens(s['output']):>8} rewrites={s['rewrites']:>3}")

    print("\n[会话逐轮消耗]" if zh else "\n[SESSION TURN USAGE]")
    print_rule()
    ranked_sessions = sorted(session_rows, key=lambda x:x['processed'], reverse=True)[:top_n]
    for session in ranked_sessions:
        sid = session["session_id"][:12]
        print(f"\n{('会话' if zh else 'Session')} {sid}")
        turns = session.get("turns", [])
        if not turns:
            print("  无可归属调用" if zh else "  No attributable calls")
            continue
        for turn in turns:
            when = turn.get("user_input_at") or turn.get("activity_start")
            stamp = when.astimezone().strftime("%H:%M:%S") if isinstance(when, datetime) else "-"
            print(f"  #{turn['round']} {stamp}  {turn['title']}")
            if zh:
                print(
                    f"     调用 {turn['calls']}｜处理输入 {human_tokens(turn['processed'])}｜"
                    f"缓存读 {human_tokens(turn['cache_read'])}｜缓存写 {human_tokens(turn['cache_write'])}｜"
                    f"输出 {human_tokens(turn['output'])}｜峰值ctx {human_tokens(turn['peak_context'])}｜"
                    f"重写 {turn['rewrites']}｜思考~{human_tokens(turn['thinking'])}"
                )
            else:
                print(
                    f"     calls {turn['calls']} | processed {human_tokens(turn['processed'])} | "
                    f"cacheR {human_tokens(turn['cache_read'])} | cacheW {human_tokens(turn['cache_write'])} | "
                    f"out {human_tokens(turn['output'])} | peak {human_tokens(turn['peak_context'])} | "
                    f"rewrites {turn['rewrites']} | thinking~{human_tokens(turn['thinking'])}"
                )

    print("\n[周期内最大的缓存重写]" if zh else "\n[LARGEST CACHE REWRITES IN PERIOD]")
    print_rule()
    rw = sorted([c for c in all_period_calls if is_full_rewrite(c)],key=lambda c:c.cache_write,reverse=True)[:top_n]
    if not rw:
        print("无。" if zh else "None.")
    else:
        if zh: print(f"{'#':>3} {'时间':<17} {'上下文':>9} {'缓存写':>9} {'模型':<22} {'执行体':<12}")
        for i,c in enumerate(rw,1):
            if zh:
                print(f"{i:>3} {fmt_short_dt(call_dt(c)):<17} {human_tokens(c.context_input):>9} {human_tokens(c.cache_write):>9} {c.model[:22]:<22} {actor_label(c.actor,lang):<12}")
            else:
                print(f"{i:>3} {fmt_short_dt(call_dt(c)):<17} ctx={human_tokens(c.context_input):>9} cacheW={human_tokens(c.cache_write):>9} {c.model}")

    print("\n[周期动态内容估算]" if zh else "\n[PERIOD DYNAMIC CONTENT ESTIMATE]")
    print_rule()
    for name,val in aggregate_dynamic.most_common(20):
        print(f"{dynamic_label(name,lang):<58} ~{human_tokens(val):>9}")
    if zh:
        print("说明：字符启发式估算，仅用于趋势归因；不含隐藏系统提示、工具 schema 和服务端压缩细节。")
    else:
        print("Note: character-based heuristic for attribution trends; hidden prompts, tool schemas, and server compaction are excluded.")

    print("\n[逐调用时间线]" if zh else "\n[PER-CALL TIMELINE]")
    print_rule()
    if zh:
        print(f"{'时间':<17} {'会话':<10} {'模型':<20} {'上下文':>9} {'缓存读':>9} {'缓存写':>9} {'输出':>8} {'重写率':>8} {'TTL':>6}")
    for c in sorted(all_period_calls, key=lambda x: call_dt(x) or start):
        details = cache_rewrite_details(c)
        sid = c.actor.split(":", 1)[-1].split("/", 1)[0][:8]
        marker = "*" if details["is_full_rewrite"] else " "
        if zh:
            print(f"{fmt_short_dt(call_dt(c)):<17} {sid:<10} {c.model[:20]:<20} {human_tokens(c.context_input):>9} {human_tokens(c.cache_read):>9} {human_tokens(c.cache_write):>9} {human_tokens(c.output_tokens):>8} {details['rewrite_ratio']*100:>7.1f}% {details['ttl']:>6}{marker}")
        else:
            print(f"{fmt_short_dt(call_dt(c))} {sid} {c.model} ctx={human_tokens(c.context_input)} cacheR={human_tokens(c.cache_read)} cacheW={human_tokens(c.cache_write)} out={human_tokens(c.output_tokens)} rewrite={details['rewrite_ratio']*100:.1f}% ttl={details['ttl']}{marker}")
    if zh:
        print("* = FULL_REWRITE；processed input 是本地归因指标，不等同于订阅额度的精确扣减。")
    else:
        print("* = FULL_REWRITE; processed input is a local attribution metric, not exact subscription billing.")

    print("\n[模型处理输入贡献]" if zh else "\n[MODEL PROCESSED-INPUT SHARE]")
    print_rule()
    for model,val in models_processed.most_common():
        pct=100*val/max(1,totals['processed'])
        print(f"{model:<28} {human_tokens(val):>10}  {pct:>6.1f}%")

    if limit_incidents:
        print("\n[周期内检测到的额度事件]" if zh else "\n[LIMIT EVENTS IN PERIOD]")
        for e in sorted(limit_incidents,key=lambda x:x.get('time') or start):
            attempts = int(e.get("attempts", 1))
            suffix = f"（原始受阻尝试 {attempts} 次）" if zh and attempts > 1 else (f" ({attempts} blocked attempts)" if attempts > 1 else "")
            print(f"- {fmt_dt(e.get('time'))}  session={e.get('session_id','')[:12]}  {e.get('kind')}{suffix}")

    report = {
        "profiler_version":__version__, "mode":"period", "language":lang,
        "period":{"start":fmt_dt(start),"end":fmt_dt(end),"source":source,"exact":bool(exact_window)},
        "account_five_hour_window": None if not exact_window else {
            "start":fmt_dt(exact_window['start']),"end":fmt_dt(exact_window['end']),
            "used_percentage_max":exact_window.get('used_percentage_max'),
            "used_percentage_last":exact_window.get('used_percentage_last'),
            "observations":exact_window.get('observations'),
        },
        "totals":totals,"diagnosis":diag,"dynamic_estimate":dict(aggregate_dynamic),
        "period_diagnosis": {k:(fmt_dt(v) if isinstance(v,datetime) else v) for k,v in period_summary.items()},
        "call_timeline": [call_timeline_record(c) for c in sorted(all_period_calls, key=lambda x: call_dt(x) or start)],
        "models_processed":dict(models_processed),
        "sessions":[{
            **s,
            "path":str(s['path']), "cwd":str(s['cwd']),
            "first":fmt_dt(s['first']), "last":fmt_dt(s['last']),
            "turns":[{
                **turn,
                "user_input_at":fmt_dt(turn.get("user_input_at")),
                "activity_start":fmt_dt(turn.get("activity_start")),
                "activity_end":fmt_dt(turn.get("activity_end")),
            } for turn in s.get("turns", [])],
        } for s in session_rows],
        "limit_events":[{**e,"time":fmt_dt(e.get('time')),"last_time":fmt_dt(e.get('last_time')),"reset":fmt_dt(e.get('reset')) if e.get('reset') else None} for e in limit_incidents],
        "parse_errors":parse_errors_total,
    }
    return report


def main() -> int:
    # Observation thresholds describe the distribution; they are not risk limits.
    ap = argparse.ArgumentParser(description="Claude Code Token Profiler：单会话 + 时间周期聚合分析")
    ap.add_argument("--context-threshold", type=int, action="append", metavar="TOKENS", help="repeatable context observation threshold; defaults to 100000, 200000, 500000")
    ap.add_argument("session_id", nargs="?", help="单会话模式：Claude Code session UUID")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--lang", choices=("zh","en"), default="zh", help="输出语言，默认 zh")
    ap.add_argument("--claude-home", type=Path, default=Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home()/".claude")))
    ap.add_argument("--search-depth", type=int, default=2, help="projects 下最多递归层数，默认 2")
    ap.add_argument("--top", type=int, default=12, help="显示热点/排名行数，默认 12")
    ap.add_argument("--query-cli", action="store_true", help="仅单会话模式：显式调用 /context all 与 /usage；可能污染会话")
    ap.add_argument("--no-subagents", action="store_true", help="不统计 subagent JSONL")
    ap.add_argument("--timeout", type=int, default=25, help="CLI 命令超时秒数")
    ap.add_argument("--json-out", type=Path, help="额外输出机器可读 JSON 报告")
    ap.add_argument("--statusline-capture", action="store_true", help="statusLine 模式：从 stdin 采集 5h/context/cost 快照")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--period", nargs=2, metavar=("START","END"), help="周期聚合：扫描所有本机会话，例如 --period '2026-08-13 18:00' '2026-08-13 23:00'")
    mode.add_argument("--last-hours", type=float, metavar="HOURS", help="周期聚合：最近 N 小时")
    mode.add_argument("--current-five-hour", action="store_true", help="周期聚合：优先 statusLine；无 statusLine 时从最新 JSONL reset 提示推断当前 5h 周期")
    mode.add_argument("--five-hour-at", metavar="DATETIME", help="周期聚合：优先 statusLine；否则从历史 JSONL reset 锚点定位包含该时间的 5h 窗口")
    mode.add_argument("--list-five-hour-windows", action="store_true", help="列出 statusLine + 历史 JSONL 可识别的 5h 窗口")
    args = ap.parse_args()
    if args.context_threshold and any(value < 0 for value in args.context_threshold):
        ap.error("--context-threshold must be non-negative")
    context_thresholds = args.context_threshold or DEFAULT_CONTEXT_THRESHOLDS
    zh=args.lang=="zh"
    claude_home=args.claude_home.expanduser().resolve()

    if args.statusline_capture:
        return capture_statusline(claude_home,args.lang)
    if args.list_five_hour_windows:
        return print_known_windows(claude_home,args.lang,max(0,args.search_depth),not args.no_subagents)

    period_requested = bool(args.period or args.last_hours is not None or args.current_five_hour or args.five_hour_at)
    if period_requested:
        if args.session_id:
            ap.error("period mode aggregates all sessions; do not also pass session_id")
        if args.query_cli:
            ap.error("--query-cli is only supported in single-session mode")
        exact: Optional[Dict[str,Any]] = None
        if args.period:
            try:
                start=parse_cli_datetime(args.period[0]); end=parse_cli_datetime(args.period[1])
            except ValueError as e:
                ap.error(str(e))
            source="manual"
        elif args.last_hours is not None:
            if args.last_hours <= 0: ap.error("--last-hours must be > 0")
            end=datetime.now().astimezone(); start=end-timedelta(hours=args.last_hours); source=f"last-{args.last_hours:g}-hours"
        elif args.current_five_hour:
            exact=resolve_current_five_hour_window(
                claude_home,max(0,args.search_depth),not args.no_subagents
            )
            if not exact:
                ap.error("无法从 statusLine 或历史 JSONL 推断当前 5 小时周期；可使用 --period 手工指定范围。")
            start,end=exact['start'],exact['end']; source=exact.get("source","inferred-current-five-hour")
        else:
            try: when=parse_cli_datetime(args.five_hour_at)
            except ValueError as e: ap.error(str(e))
            exact=resolve_five_hour_window_at(
                claude_home,when,max(0,args.search_depth),not args.no_subagents
            )
            if not exact:
                ap.error("无法从 statusLine 或历史 JSONL 找到包含该时间点的 5 小时窗口；可使用 --period 手工指定。")
            start,end=exact['start'],exact['end']; source=exact.get("source","inferred-five-hour-at")
        if end <= start: ap.error("period END must be after START")
        report=analyze_period(claude_home,start,end,max(0,args.search_depth),max(1,args.top),args.lang,not args.no_subagents,source,exact,context_thresholds)
        if args.json_out:
            out=args.json_out.expanduser().resolve(); out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(f"\n{'JSON 报告已写入' if zh else 'JSON report written'}: {out}")
        return 0

    if not args.session_id:
        ap.error("provide session_id for single-session mode, or choose a period mode")

    main_path=locate_session(args.session_id,claude_home,max(0,args.search_depth))
    main_rows,main_errors=load_jsonl(main_path)
    cwd=detect_cwd(main_rows,Path.cwd())
    print(f"{'Profiler 版本' if zh else 'Profiler version'} : {__version__}")
    print(f"{'会话 ID' if zh else 'Session ID'}       : {args.session_id}")
    print(f"{'会话文件' if zh else 'Transcript'}      : {main_path}")
    print(f"{'项目目录' if zh else 'Project cwd'}      : {cwd}")
    print(f"{'CLI 查询' if zh else 'CLI query'}       : {'已启用' if zh and args.query_cli else ('未启用（只读）' if zh else ('enabled' if args.query_cli else 'disabled (read-only)'))}")

    context_info=None; live_usage_raw=None; before=file_fingerprint(main_path) if args.query_cli else None
    if args.query_cli:
        claude_bin=shutil.which("claude")
        if not claude_bin:
            print("\n未在 PATH 中找到 claude；继续只分析 JSONL。" if zh else "\n'claude' not found; JSONL only.")
        else:
            ok_ctx,out_ctx,err_ctx,_=run_claude_local_command(claude_bin,cwd,args.session_id,"/context all",args.timeout)
            print_live_section("LIVE /context all",ok_ctx,out_ctx,err_ctx)
            if out_ctx: context_info=parse_context_output(out_ctx)
            ok_usage,out_usage,err_usage,_=run_claude_local_command(claude_bin,cwd,args.session_id,"/usage",args.timeout)
            print_live_section("LIVE /usage",ok_usage,out_usage,err_usage)
            if out_usage: live_usage_raw=out_usage
        if before != file_fingerprint(main_path):
            print("\n警告：CLI 查询期间主会话 JSONL 发生变化；建议不带 --query-cli 重跑。" if zh else "\nWARNING: transcript changed during CLI query; rerun read-only.",file=sys.stderr)

    analyses=[analyze_transcript(main_path,"main")]
    if not args.no_subagents:
        for sp in find_subagent_files(main_path,args.session_id): analyses.append(analyze_transcript(sp,sp.stem.replace("agent-","agent:")))
    report=report_analysis(analyses,context_info,max(1,args.top),args.lang,claude_home,args.session_id,main_path,main_rows,context_thresholds)
    report.update({"session_id":args.session_id,"main_transcript":str(main_path),"project_cwd":str(cwd),"live_usage_raw":live_usage_raw,"main_parse_errors_initial":main_errors})
    if args.json_out:
        out=args.json_out.expanduser().resolve(); out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f"\n{'JSON 报告已写入' if zh else 'JSON report written'}: {out}")

    print("\n[解读]" if zh else "\n[INTERPRETATION]")
    print_rule()
    if zh:
        print("- 单会话模式定位具体哪次调用、thinking、tool result 与缓存重写最重。")
        print("- 周期模式会扫描本机 projects 下所有会话，并只统计落在时间范围内的 API 调用。")
        print("- FULL_REWRITE 表示大段 Prompt Cache 未命中并重新写入；它不是等量的上下文新增。")
        print("- 5h 边界优先级：statusLine resets_at > JSONL 明确 resets 时间 > limit+下一次成功调用区间。")
    else:
        print("- Session mode diagnoses individual requests; period mode aggregates all local sessions in a time range.")
        print("- FULL_REWRITE is a large prompt-cache miss/rewrite, not equivalent context growth.")
        print("- 5h boundary priority: statusLine resets_at > explicit transcript reset text > limit/next-success bracket.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
