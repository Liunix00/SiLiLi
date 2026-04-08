"""行级 unified diff → Markdown 包装。"""
from __future__ import annotations

import difflib
from datetime import date


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


def wrap_diff_as_markdown(
    diff_text: str,
    title: str,
    old_path: str,
    new_path: str,
) -> str:
    if not diff_text.strip():
        return (
            f"# {title}\n"
            f"> 生成时间：{date.today().isoformat()}\n\n"
            "无变更。\n"
        )
    return (
        f"# {title}\n"
        f"> 生成时间：{date.today().isoformat()}\n"
        f"> 对比基准：`{old_path}` ↔ `{new_path}`\n\n"
        f"```diff\n{diff_text}\n```\n"
    )
