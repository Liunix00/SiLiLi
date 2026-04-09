"""Diff 工具：语义化变更摘要 + unified diff 附录。"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Unified diff（保留原有功能）
# ---------------------------------------------------------------------------

def generate_unified_diff(
    old_text: str,
    new_text: str,
    old_label: str = "旧版本",
    new_label: str = "新版本",
) -> str:
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=old_label, tofile=new_label,
        lineterm="",
    ))
    if not diff_lines:
        return ""
    return "\n".join(diff_lines)


# ---------------------------------------------------------------------------
# Plan 行解析
# ---------------------------------------------------------------------------

_TASK_LINE_RE = re.compile(
    r"^(\s*)-\s*(T-\d{3}-\d{3}(?:-\d{2})?)\s+(.+?)\s*[｜|]\s*(.+)$"
)
_STATUS_PATTERNS = [
    ("✅ 已完成", "✅"),
    ("🟡 进行中", "🟡"),
    ("🔴 阻塞", "🔴"),
    ("⚪ 未开始", "⚪"),
]
_DAYS_RE = re.compile(r"(\d+\.?\d*)d")


@dataclass
class TaskInfo:
    task_id: str
    name: str
    status: str
    days: str
    indent: int
    raw_meta: str


def _parse_plan_tasks(text: str) -> Dict[str, TaskInfo]:
    tasks: Dict[str, TaskInfo] = {}
    for line in text.splitlines():
        m = _TASK_LINE_RE.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        task_id = m.group(2)
        name = m.group(3).strip()
        meta = m.group(4).strip()

        status = "未知"
        for label, emoji in _STATUS_PATTERNS:
            if label in meta or emoji in meta:
                status = label
                break

        days_m = _DAYS_RE.search(meta)
        days = days_m.group(1) if days_m else ""

        tasks[task_id] = TaskInfo(
            task_id=task_id, name=name, status=status,
            days=days, indent=indent, raw_meta=meta,
        )
    return tasks


# ---------------------------------------------------------------------------
# Progress 段落解析
# ---------------------------------------------------------------------------

_PROGRESS_TASK_RE = re.compile(r"^##\s+(T-\d{3}-\d{3}(?:-\d{2})?)\s+(.*)")
_PROGRESS_DATE_RE = re.compile(r"^###\s+(\d{4}\.\d{1,2}\.\d{1,2})")


def _parse_progress_sections(
    text: str,
) -> Dict[str, Dict[str, List[str]]]:
    """返回 {task_id: {date: [items]}}"""
    sections: Dict[str, Dict[str, List[str]]] = {}
    current_task: Optional[str] = None
    current_date: Optional[str] = None

    for line in text.splitlines():
        tm = _PROGRESS_TASK_RE.match(line)
        if tm:
            current_task = tm.group(1)
            current_date = None
            sections.setdefault(current_task, {})
            continue

        dm = _PROGRESS_DATE_RE.match(line)
        if dm and current_task:
            current_date = dm.group(1)
            sections[current_task].setdefault(current_date, [])
            continue

        if (
            current_task
            and current_date
            and line.strip().startswith("-")
        ):
            sections[current_task][current_date].append(
                line.strip().lstrip("- ").strip()
            )

    return sections


# ---------------------------------------------------------------------------
# 语义化 Diff
# ---------------------------------------------------------------------------

def generate_semantic_diff(
    old_text: str,
    new_text: str,
    doc_type: str,
) -> str:
    if doc_type == "plan":
        return _semantic_plan_diff(old_text, new_text)
    if doc_type == "progress":
        return _semantic_progress_diff(old_text, new_text)
    return ""


def _semantic_plan_diff(old_text: str, new_text: str) -> str:
    old_tasks = _parse_plan_tasks(old_text)
    new_tasks = _parse_plan_tasks(new_text)

    status_changes: List[str] = []
    new_items: List[str] = []
    days_changes: List[str] = []

    for tid, new_t in new_tasks.items():
        if tid not in old_tasks:
            kind = "子任务" if new_t.indent > 0 else "父任务"
            new_items.append(
                f"- {tid} {new_t.name}（{kind}，{new_t.status}）"
            )
        else:
            old_t = old_tasks[tid]
            if old_t.status != new_t.status:
                status_changes.append(
                    f"- {tid} {new_t.name}: {old_t.status} → {new_t.status}"
                )
            if old_t.days and new_t.days and old_t.days != new_t.days:
                days_changes.append(
                    f"- {tid} {new_t.name}: {old_t.days}d → {new_t.days}d"
                )

    if not status_changes and not new_items and not days_changes:
        return "无语义变更。"

    parts: List[str] = []
    if status_changes:
        parts.append("### 状态变更\n" + "\n".join(status_changes))
    if new_items:
        parts.append("### 新增任务\n" + "\n".join(new_items))
    if days_changes:
        parts.append("### 工时变更\n" + "\n".join(days_changes))

    return "\n\n".join(parts)


def _semantic_progress_diff(old_text: str, new_text: str) -> str:
    old_sec = _parse_progress_sections(old_text)
    new_sec = _parse_progress_sections(new_text)

    additions: List[str] = []

    for tid, dates in new_sec.items():
        if tid not in old_sec:
            additions.append(f"### 新增 {tid} 段落")
            for d, items in dates.items():
                additions.append(f"#### {d}")
                additions.extend(f"- {it}" for it in items)
        else:
            for d, items in dates.items():
                if d not in old_sec[tid]:
                    additions.append(f"### {tid} | {d}")
                    additions.extend(f"- {it}" for it in items)
                else:
                    old_items = set(old_sec[tid][d])
                    new_only = [it for it in items if it not in old_items]
                    if new_only:
                        additions.append(f"### {tid} | {d}（追加）")
                        additions.extend(f"- {it}" for it in new_only)

    if not additions:
        return "无新增进展。"

    return "\n".join(additions)


# ---------------------------------------------------------------------------
# Markdown 输出包装
# ---------------------------------------------------------------------------

def wrap_diff_as_markdown(
    semantic_diff: str,
    unified_diff_text: str,
    title: str,
    old_path: str,
    new_path: str,
) -> str:
    today_str = date.today().isoformat()

    if not unified_diff_text.strip() and semantic_diff.startswith("无"):
        return (
            f"# {title}\n"
            f"> 生成时间：{today_str}\n\n"
            "无变更。\n"
        )

    parts = [
        f"# {title}",
        f"> 生成时间：{today_str}",
        f"> 对比基准：`{old_path}` ↔ `{new_path}`",
        "",
        "## 变更摘要",
        "",
        semantic_diff,
    ]

    if unified_diff_text.strip():
        parts.extend([
            "",
            "<details><summary>完整 Unified Diff</summary>",
            "",
            f"```diff\n{unified_diff_text}\n```",
            "",
            "</details>",
        ])

    return "\n".join(parts) + "\n"
