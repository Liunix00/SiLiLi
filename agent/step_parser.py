"""Steps 结构化解析器：将原始 Steps 文本解析为结构化数据，不使用 LLM。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class StepAction:
    """Steps 中的子行动作。"""
    type: str
    content: str
    params: Dict[str, str] = field(default_factory=dict)


@dataclass
class StepEntry:
    """一条项目步骤记录。"""
    date: str
    project_id: str
    person: str
    description: str
    actions: List[StepAction] = field(default_factory=list)


_DATE_HEADING_RE = re.compile(
    r"^##\s*\**\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
)
_PROJECT_TAG_RE = re.compile(
    r"^-\s*[\u3010\[](\d{3}-[^\u3011\]]+)[\u3011\]]\s*(.*)"
)
_FILE_HEADER_RE = re.compile(r"^===\s*(.+?)\s*===$")
_ACTION_LINE_RE = re.compile(r"^\s{4,}-\s+(.+)")

_DONE_RE = re.compile(r"^done\b\s*(.*)", re.IGNORECASE)
_TODO_RE = re.compile(r"^todo\b\s*(.*)", re.IGNORECASE)
_BLOCKED_RE = re.compile(r"^blocked\s+by\b\s+(.*)", re.IGNORECASE)
_UNBLOCK_RE = re.compile(r"^unblock\b\s+(.*)", re.IGNORECASE)
_PLAN_RE = re.compile(r"^plan\b\s+(.*)", re.IGNORECASE)
_IDEA_RE = re.compile(r"^idea\b\s+(.*)", re.IGNORECASE)
_RISK_RE = re.compile(r"^risk\b\s+(.*)", re.IGNORECASE)


def _parse_todo_params(raw: str) -> Dict[str, str]:
    """解析 todo 参数：描述[，工时[，add]]"""
    params: Dict[str, str] = {}
    if not raw.strip():
        params["description"] = ""
        return params

    parts = re.split(r"[，,]", raw)
    desc_parts = list(parts)

    if desc_parts and desc_parts[-1].strip().lower() == "add":
        params["add"] = "true"
        desc_parts.pop()

    if desc_parts:
        last = desc_parts[-1].strip()
        days_match = re.search(r"([\d.]+)\s*[天d日]", last)
        if days_match:
            params["days"] = days_match.group(1)
            remaining = re.sub(r"\s*([\d.]+)\s*[天d日]\s*", "", last).strip()
            if remaining:
                desc_parts[-1] = remaining
            else:
                desc_parts.pop()

    params["description"] = "，".join(p.strip() for p in desc_parts if p.strip())
    return params


def _parse_plan_params(raw: str) -> Dict[str, str]:
    """解析 plan 参数：任务名称：xxx，负责人：xxx，工期：xd，ddl：xx/xx"""
    params: Dict[str, str] = {}
    kv_pattern = re.compile(
        r"(任务名称|任务描述|负责人|工期|ddl)\s*[：:]\s*([^，,]+)"
    )
    key_map = {
        "任务名称": "name", "任务描述": "description",
        "负责人": "owner", "工期": "days", "ddl": "ddl",
    }
    for m in kv_pattern.finditer(raw):
        params[key_map.get(m.group(1), m.group(1))] = m.group(2).strip()
    if not params:
        params["description"] = raw
    return params


def _parse_action(text: str) -> StepAction:
    """将子行文本解析为 StepAction。"""
    text = text.strip()

    m = _DONE_RE.match(text)
    if m:
        return StepAction(type="done", content=m.group(1).strip())

    m = _TODO_RE.match(text)
    if m:
        raw = m.group(1).strip()
        params = _parse_todo_params(raw)
        return StepAction(
            type="todo",
            content=params.get("description", raw),
            params=params,
        )

    m = _BLOCKED_RE.match(text)
    if m:
        entity = m.group(1).strip()
        return StepAction(type="blocked", content=entity, params={"entity": entity})

    m = _UNBLOCK_RE.match(text)
    if m:
        entity = m.group(1).strip()
        return StepAction(type="unblock", content=entity, params={"entity": entity})

    m = _PLAN_RE.match(text)
    if m:
        raw = m.group(1).strip()
        return StepAction(type="plan", content=raw, params=_parse_plan_params(raw))

    m = _IDEA_RE.match(text)
    if m:
        return StepAction(type="idea", content=m.group(1).strip())

    m = _RISK_RE.match(text)
    if m:
        return StepAction(type="risk", content=m.group(1).strip())

    return StepAction(type="note", content=text)


def _extract_person_from_path(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    for i, part in enumerate(parts):
        if part.endswith(".md") and i > 0:
            return parts[i - 1]
    return "未知"


def parse_all_steps(raw_text: str) -> Dict[str, List[StepEntry]]:
    """将拼接后的 steps 原始文本解析为按项目 ID 分组的结构化数据。

    raw_text 可能包含多个文件，以 ``=== 路径 ===`` 分隔。
    """
    result: Dict[str, List[StepEntry]] = {}
    current_person = "未知"
    current_date = "未知日期"
    current_entry: Optional[StepEntry] = None

    for line in raw_text.splitlines():
        file_m = _FILE_HEADER_RE.match(line)
        if file_m:
            current_person = _extract_person_from_path(file_m.group(1))
            current_entry = None
            continue

        date_m = _DATE_HEADING_RE.match(line)
        if date_m:
            y, mo, d = date_m.group(1), date_m.group(2), date_m.group(3)
            current_date = f"{y}.{int(mo)}.{int(d)}"
            current_entry = None
            continue

        proj_m = _PROJECT_TAG_RE.match(line)
        if proj_m:
            project_id = proj_m.group(1)
            description = proj_m.group(2).strip()
            current_entry = StepEntry(
                date=current_date,
                project_id=project_id,
                person=current_person,
                description=description,
            )
            result.setdefault(project_id, []).append(current_entry)
            continue

        action_m = _ACTION_LINE_RE.match(line)
        if action_m and current_entry is not None:
            current_entry.actions.append(_parse_action(action_m.group(1)))
            continue

        if line.strip():
            current_entry = None

    return result


def format_steps_for_llm(entries: List[StepEntry]) -> str:
    """将结构化 steps 格式化为 LLM 友好的文本，清晰标注动作类型。"""
    if not entries:
        return ""

    lines: List[str] = []
    current_date = ""

    for entry in entries:
        if entry.date != current_date:
            current_date = entry.date
            lines.append(f"\n### {current_date}")

        lines.append(f"- [{entry.person}] {entry.description}")

        for action in entry.actions:
            if action.type == "done":
                target = action.content if action.content else "（整体完成）"
                lines.append(f"    - [完成] {target}")
            elif action.type == "todo":
                days = action.params.get("days", "")
                add = "，累加工时" if action.params.get("add") else ""
                suffix = f"（{days}天{add}）" if days else ""
                lines.append(f"    - [待办] {action.content}{suffix}")
            elif action.type == "blocked":
                lines.append(f"    - [阻塞] 被 {action.content} 阻塞")
            elif action.type == "unblock":
                lines.append(f"    - [解除阻塞] {action.content}")
            elif action.type == "plan":
                parts = []
                p = action.params
                if p.get("name"):
                    parts.append(f"任务: {p['name']}")
                if p.get("owner"):
                    parts.append(f"负责人: {p['owner']}")
                if p.get("days"):
                    parts.append(f"工期: {p['days']}")
                if p.get("ddl"):
                    parts.append(f"截止: {p['ddl']}")
                detail = "，".join(parts) if parts else action.content
                lines.append(f"    - [新计划] {detail}")
            elif action.type == "idea":
                lines.append(f"    - [灵感] {action.content}")
            elif action.type == "risk":
                lines.append(f"    - [风险] {action.content}")
            else:
                lines.append(f"    - {action.content}")

    return "\n".join(lines).strip()


def extract_ideas(entries: List[StepEntry]) -> Dict[str, List[str]]:
    """从结构化 entries 中提取 idea，按日期分组。"""
    ideas: Dict[str, List[str]] = {}
    for entry in entries:
        for action in entry.actions:
            if action.type == "idea":
                ideas.setdefault(entry.date, []).append(action.content)
    return ideas
