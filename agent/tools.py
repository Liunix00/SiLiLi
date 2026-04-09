"""司理理 Agent 工具注册表与实现。"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

from base_structure.llms.conversation_system import ConversationRequest
from base_structure.llms.llm_client import AsyncOpenAIClient
from base_structure.utils.readonly_fs import get_humannote_root

from agent.diff_utils import generate_unified_diff, wrap_diff_as_markdown
from agent.state_manager import StateManager

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _robot_root() -> Path:
    raw = os.environ.get("ROBOTNOTE_ROOT", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p.resolve() if p.is_absolute() else (Path.cwd() / p).resolve()
    return (_REPO_ROOT / "RobotNote").resolve()


# ---------------------------------------------------------------------------
# ToolContext: 工具共享的运行时资源
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    repo_root: Path
    human_root: Path
    robot_root: Path
    gen_llm_client: AsyncOpenAIClient
    plan_prompt_template: str
    progress_prompt_template: str
    gen_params: Dict[str, Any]
    state_mgr: StateManager
    all_steps_content: Optional[str] = None
    today: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    project_filter: Optional[str] = None
    person_filter: Optional[str] = None


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

async def tool_list_projects(ctx: ToolContext, **_: Any) -> str:
    projects_dir = ctx.human_root / "Projects"
    if not projects_dir.is_dir():
        return "错误：HumanNote/Projects 目录不存在"
    dirs = sorted(
        d.name for d in projects_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
    if ctx.project_filter:
        dirs = [d for d in dirs if d == ctx.project_filter]
    if not dirs:
        return "Projects 目录下无匹配项目"
    return "项目列表：\n" + "\n".join(f"- {d}" for d in dirs)


async def tool_read_file(ctx: ToolContext, *, path: str = "", **_: Any) -> str:
    if not path:
        return "错误：请提供 path 参数"
    target = (ctx.human_root / path).resolve()
    if not target.is_file():
        return f"错误：文件不存在 - {path}"
    try:
        return target.read_text(encoding="utf-8")
    except Exception as exc:
        return f"读取失败: {exc}"


async def tool_read_all_steps(ctx: ToolContext, **_: Any) -> str:
    """读取上次运行以来的所有 steps，按日期过滤条目。"""
    steps_dir = ctx.human_root / "Steps"
    if not steps_dir.is_dir():
        return "错误：HumanNote/Steps 目录不存在"

    last_run = ctx.state_mgr.get_last_run_time()

    md_files: List[Path] = sorted(steps_dir.rglob("*.md"))
    if ctx.person_filter:
        md_files = [
            f for f in md_files
            if ctx.person_filter in str(f)
        ]
    if not md_files:
        return "Steps 目录下无匹配的 .md 文件"

    collected: List[str] = []
    for md in md_files:
        raw = md.read_text(encoding="utf-8")
        filtered = _filter_steps_by_date(raw, last_run)
        if filtered.strip():
            rel = md.relative_to(ctx.human_root)
            collected.append(f"=== {rel} ===\n{filtered}")

    if not collected:
        hint = f"（上次运行: {last_run.isoformat()}）" if last_run else "（首次运行，无过滤）"
        return f"没有找到新的步骤记录 {hint}"

    ctx.all_steps_content = "\n\n".join(collected)
    return ctx.all_steps_content


_DATE_HEADING_RE = re.compile(
    r"^##\s*\**\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
)


def _filter_steps_by_date(text: str, since: Optional[datetime]) -> str:
    """保留 since 之后（含）的日期段落；since 为 None 时返回全部。"""
    if since is None:
        return text

    lines = text.splitlines(keepends=True)
    result: List[str] = []
    include = False

    for line in lines:
        m = _DATE_HEADING_RE.match(line)
        if m:
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                include = dt >= since
            except ValueError:
                include = True
        if include:
            result.append(line)

    return "".join(result)


async def tool_update_project(ctx: ToolContext, *, project_id: str = "", **_: Any) -> str:
    """为指定项目生成更新后的 plan/progress/diff，写入 RobotNote。"""
    if not project_id:
        return "错误：请提供 project_id 参数（如 '001-问津'）"

    projects_dir = ctx.human_root / "Projects"
    project_dir = projects_dir / project_id
    if not project_dir.is_dir():
        return f"错误：项目目录不存在 - {project_id}"

    plan_files = list(project_dir.glob("*-plan.md"))
    progress_files = list(project_dir.glob("*-progress.md"))
    if not plan_files or not progress_files:
        return f"错误：项目 {project_id} 缺少 plan.md 或 progress.md"

    old_plan = plan_files[0].read_text(encoding="utf-8")
    old_progress = progress_files[0].read_text(encoding="utf-8")
    plan_filename = plan_files[0].name
    progress_filename = progress_files[0].name

    relevant_steps = _extract_project_steps(ctx.all_steps_content or "", project_id)
    if not relevant_steps.strip():
        return f"项目 {project_id} 在近期步骤中未被提及，跳过更新"

    new_plan = await _llm_generate(
        ctx,
        ctx.plan_prompt_template,
        old_content=old_plan,
        steps=relevant_steps,
        doc_type="plan",
        project_id=project_id,
    )

    new_progress = await _llm_generate(
        ctx,
        ctx.progress_prompt_template,
        old_content=old_progress,
        steps=relevant_steps,
        doc_type="progress",
        project_id=project_id,
        plan_content=new_plan,
    )

    # --- Idea 提取 ---
    ideas = _extract_ideas(relevant_steps, ctx.all_steps_content or "")
    idea_filename = f"{project_id}-idea.md"
    idea_written = False
    if ideas:
        old_idea_path = project_dir / idea_filename
        old_idea = ""
        if old_idea_path.is_file():
            old_idea = old_idea_path.read_text(encoding="utf-8")

        new_idea_parts: List[str] = [old_idea.rstrip()] if old_idea.strip() else [f"# {project_id} Ideas\n\n---\n"]
        for date, items in ideas.items():
            new_idea_parts.append(f"\n### {date}")
            for item in items:
                new_idea_parts.append(f"- {item}")
        new_idea = "\n".join(new_idea_parts) + "\n"

        out_dir = ctx.robot_root / "Projects" / project_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / idea_filename).write_text(new_idea, encoding="utf-8")
        idea_written = True

    # --- Diff 生成 ---
    plan_diff_raw = generate_unified_diff(
        old_plan, new_plan,
        f"HumanNote/Projects/{project_id}/{plan_filename}",
        f"RobotNote/Projects/{project_id}/{plan_filename}",
    )
    progress_diff_raw = generate_unified_diff(
        old_progress, new_progress,
        f"HumanNote/Projects/{project_id}/{progress_filename}",
        f"RobotNote/Projects/{project_id}/{progress_filename}",
    )

    plan_diff_md = wrap_diff_as_markdown(
        plan_diff_raw,
        f"{project_id} Plan 变更记录",
        f"HumanNote/Projects/{project_id}/{plan_filename}",
        f"RobotNote/Projects/{project_id}/{plan_filename}",
    )
    progress_diff_md = wrap_diff_as_markdown(
        progress_diff_raw,
        f"{project_id} Progress 变更记录",
        f"HumanNote/Projects/{project_id}/{progress_filename}",
        f"RobotNote/Projects/{project_id}/{progress_filename}",
    )

    out_dir = ctx.robot_root / "Projects" / project_id
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / plan_filename).write_text(new_plan, encoding="utf-8")
    (out_dir / progress_filename).write_text(new_progress, encoding="utf-8")

    diff_plan_name = plan_filename.replace("-plan.md", "-plan_diff.md")
    diff_progress_name = progress_filename.replace("-progress.md", "-progress_diff.md")
    (out_dir / diff_plan_name).write_text(plan_diff_md, encoding="utf-8")
    (out_dir / diff_progress_name).write_text(progress_diff_md, encoding="utf-8")

    has_plan_change = bool(plan_diff_raw.strip())
    has_progress_change = bool(progress_diff_raw.strip())
    summary_parts = []
    if has_plan_change:
        summary_parts.append("plan 已更新")
    else:
        summary_parts.append("plan 无变更")
    if has_progress_change:
        summary_parts.append("progress 已更新")
    else:
        summary_parts.append("progress 无变更")
    if idea_written:
        summary_parts.append(f"idea 已追加 {sum(len(v) for v in ideas.values())} 条")
    else:
        summary_parts.append("idea 无新增")

    output_files = [plan_filename, progress_filename, diff_plan_name, diff_progress_name]
    if idea_written:
        output_files.append(idea_filename)

    return (
        f"项目 {project_id} 处理完成（{', '.join(summary_parts)}）。\n"
        f"输出目录: RobotNote/Projects/{project_id}/\n"
        f"文件: {', '.join(output_files)}"
    )


def _extract_project_steps(all_steps: str, project_id: str) -> str:
    """从全部 steps 中提取与指定项目相关的连续块，保留日期上下文。

    匹配规则（严格全称）：【001-问津】 或 [001-问津]。
    遇到其他项目标签或非缩进行时结束当前块。
    日期标题行会在需要时插入输出，确保 LLM 能看到每条步骤的日期。
    """
    date_heading_re = re.compile(
        r"^##\s*\**\s*\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
    )
    tag_re = re.compile(
        r"[\u3010\[]" + re.escape(project_id) + r"[\u3011\]]"
    )
    any_tag_re = re.compile(r"[\u3010\[]\d{3}-[^\u3011\]]+[\u3011\]]")

    result_lines: List[str] = []
    in_block = False
    current_date_line: Optional[str] = None
    date_emitted = False

    for line in all_steps.splitlines():
        if date_heading_re.match(line):
            current_date_line = line
            date_emitted = False
            in_block = False
            continue

        if tag_re.search(line):
            if current_date_line and not date_emitted:
                result_lines.append(current_date_line)
                date_emitted = True
            in_block = True
            result_lines.append(line)
        elif any_tag_re.search(line):
            in_block = False
        elif in_block and line.startswith("    "):
            result_lines.append(line)
        else:
            in_block = False

    return "\n".join(result_lines)


def _extract_ideas(relevant_steps: str, all_steps_content: str) -> Dict[str, List[str]]:
    """从 steps 中提取 idea 行及其所属日期。

    返回 {日期字符串: [idea原文, ...]} 的有序字典。
    需要在 all_steps_content 中查找日期上下文，因为 relevant_steps
    只保留了项目标签行和缩进行，日期标题可能被过滤掉了。
    """
    date_heading_re = re.compile(
        r"^##\s*\**\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
    )
    idea_re = re.compile(r"^\s*-\s*idea\s+(.+)", re.IGNORECASE)

    date_for_line: Dict[int, str] = {}
    if all_steps_content:
        current_date = "未知日期"
        for line in all_steps_content.splitlines():
            m = date_heading_re.match(line)
            if m:
                current_date = f"{m.group(1)}.{int(m.group(2))}.{int(m.group(3))}"
            date_for_line[id(line)] = current_date

    current_date = "未知日期"
    all_lines = (all_steps_content or "").splitlines()
    line_date_map: List[str] = []
    for line in all_lines:
        m = date_heading_re.match(line)
        if m:
            current_date = f"{m.group(1)}.{int(m.group(2))}.{int(m.group(3))}"
        line_date_map.append(current_date)

    all_lines_set = {
        (i, line): line_date_map[i]
        for i, line in enumerate(all_lines)
    }

    ideas: Dict[str, List[str]] = {}
    for line in relevant_steps.splitlines():
        im = idea_re.match(line)
        if not im:
            continue
        idea_text = im.group(1).strip()
        found_date = "未知日期"
        for i, al in enumerate(all_lines):
            if line.strip() == al.strip():
                found_date = line_date_map[i]
                break
        ideas.setdefault(found_date, []).append(idea_text)

    return ideas


async def _llm_generate(
    ctx: ToolContext,
    prompt_template: str,
    *,
    old_content: str,
    steps: str,
    doc_type: str,
    project_id: str,
    plan_content: Optional[str] = None,
) -> str:
    parts = [
        f"# 项目: {project_id}",
        f"# 今日日期: {ctx.today}",
        "",
        f"## 当前 {doc_type}\n\n{old_content}",
    ]
    if plan_content is not None:
        parts.append(f"## 当前 plan（用于确认任务 ID 和名称）\n\n{plan_content}")
    parts.append(f"## 近期相关步骤\n\n{steps}")
    user_msg = "\n\n".join(parts)
    request = ConversationRequest(
        system_prompt=prompt_template,
        user_question=user_msg,
    )
    messages = request()

    try:
        response = await ctx.gen_llm_client.generate(
            messages=messages,
            stream=False,
            **ctx.gen_params,
        )
    except Exception as exc:
        logger.error("LLM 生成 %s 失败（%s）: %s", doc_type, project_id, exc)
        return old_content

    return _strip_fenced_markdown(response)


def _strip_fenced_markdown(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*$", t)
    return m.group(1).strip() if m else t


async def tool_save_run_time(ctx: ToolContext, **_: Any) -> str:
    ctx.state_mgr.save_run_time()
    return "运行时间已保存"


async def tool_finish(ctx: ToolContext, *, summary: str = "", **_: Any) -> str:
    return summary or "完成"


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

ToolFunc = Callable[..., Coroutine[Any, Any, str]]

TOOL_REGISTRY: Dict[str, ToolFunc] = {
    "list_projects": tool_list_projects,
    "read_file": tool_read_file,
    "read_all_steps": tool_read_all_steps,
    "update_project": tool_update_project,
    "save_run_time": tool_save_run_time,
    "finish": tool_finish,
}

TOOL_DESCRIPTIONS = {
    "list_projects": "list_projects() — 列出 HumanNote/Projects 下所有项目目录",
    "read_file": 'read_file(path="相对路径") — 读取 HumanNote 中指定文件内容',
    "read_all_steps": "read_all_steps() — 读取上次运行以来的全部工作步骤",
    "update_project": 'update_project(project_id="001-问津") — 为指定项目生成更新的 plan/progress/idea 及 diff，写入 RobotNote',
    "save_run_time": "save_run_time() — 记录本次运行时间戳",
    "finish": 'finish(summary="总结文字") — 宣告本次运行完成',
}
