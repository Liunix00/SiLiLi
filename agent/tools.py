"""司理理 Agent 工具注册表与实现。"""
from __future__ import annotations

import asyncio
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

from agent.diff_utils import (
    generate_semantic_diff,
    generate_unified_diff,
    wrap_diff_as_markdown,
)
from agent.state_manager import StateManager
from agent.step_parser import (
    StepEntry,
    extract_ideas,
    format_steps_for_llm,
    parse_all_steps,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _robot_root() -> Path:
    raw = os.environ.get("ROBOTNOTE_ROOT", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p.resolve() if p.is_absolute() else (Path.cwd() / p).resolve()
    return (_REPO_ROOT / "RobotNote").resolve()


# ---------------------------------------------------------------------------
# 运行时数据结构
# ---------------------------------------------------------------------------

@dataclass
class ProjectOutput:
    """单个项目的生成结果，供审阅/修订使用。"""
    project_id: str
    old_plan: str
    old_progress: str
    new_plan: str
    new_progress: str
    plan_semantic_diff: str
    progress_semantic_diff: str
    plan_unified_diff: str
    progress_unified_diff: str
    ideas: Dict[str, List[str]]
    formatted_steps: str
    plan_filename: str
    progress_filename: str


@dataclass
class ReviewResult:
    """单个项目的审阅结果。"""
    project_id: str
    passed: bool
    plan_issues: str
    progress_issues: str
    raw_feedback: str


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
    review_llm_client: AsyncOpenAIClient
    revision_llm_client: AsyncOpenAIClient
    review_prompt_template: str
    revision_prompt_template: str
    review_params: Dict[str, Any]
    revision_params: Dict[str, Any]

    all_steps_content: Optional[str] = None
    today: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    project_filter: Optional[str] = None
    person_filter: Optional[str] = None
    max_review_rounds: int = 3

    parsed_steps: Dict[str, List[StepEntry]] = field(default_factory=dict)
    project_outputs: Dict[str, ProjectOutput] = field(default_factory=dict)
    review_feedback: Dict[str, ReviewResult] = field(default_factory=dict)
    review_round: int = 0


# ---------------------------------------------------------------------------
# 基础工具（保持不变）
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


_DATE_HEADING_RE = re.compile(
    r"^##\s*\**\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
)


async def tool_read_all_steps(ctx: ToolContext, **_: Any) -> str:
    """读取上次运行以来的所有 steps，按日期过滤条目。"""
    steps_dir = ctx.human_root / "Steps"
    if not steps_dir.is_dir():
        return "错误：HumanNote/Steps 目录不存在"

    last_run = ctx.state_mgr.get_last_run_time()

    md_files: List[Path] = sorted(steps_dir.rglob("*.md"))
    if ctx.person_filter:
        md_files = [f for f in md_files if ctx.person_filter in str(f)]
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
        hint = (
            f"（上次运行: {last_run.isoformat()}）"
            if last_run
            else "（首次运行，无过滤）"
        )
        return f"没有找到新的步骤记录 {hint}"

    ctx.all_steps_content = "\n\n".join(collected)
    return ctx.all_steps_content


def _filter_steps_by_date(text: str, since: Optional[datetime]) -> str:
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


# ---------------------------------------------------------------------------
# update_all_projects：并行生成所有活跃项目的 plan/progress
# ---------------------------------------------------------------------------

async def tool_update_all_projects(ctx: ToolContext, **_: Any) -> str:
    """代码解析 steps → 并行 LLM 生成所有活跃项目的 plan/progress/idea → 写入 RobotNote。"""
    if not ctx.all_steps_content:
        return "错误：请先调用 read_all_steps 读取步骤"

    parsed = parse_all_steps(ctx.all_steps_content)
    ctx.parsed_steps = parsed

    if not parsed:
        return "近期步骤中未涉及任何项目，无需更新"

    active_ids = sorted(parsed.keys())
    if ctx.project_filter:
        active_ids = [p for p in active_ids if p == ctx.project_filter]

    if not active_ids:
        return "过滤后无匹配项目"

    tasks = [
        _process_single_project(ctx, pid, parsed[pid])
        for pid in active_ids
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    summary_lines: List[str] = []
    for pid, result in zip(active_ids, results):
        if isinstance(result, Exception):
            logger.error("项目 %s 处理失败: %s", pid, result, exc_info=result)
            summary_lines.append(f"- {pid}: 处理失败（{result}）")
        else:
            summary_lines.append(f"- {pid}: {result}")

    return f"所有项目处理完成（共 {len(active_ids)} 个）：\n" + "\n".join(
        summary_lines
    )


async def _process_single_project(
    ctx: ToolContext,
    project_id: str,
    entries: List[StepEntry],
) -> str:
    """处理单个项目：LLM 生成 plan → progress，提取 idea，写文件。"""
    project_dir = ctx.human_root / "Projects" / project_id
    if not project_dir.is_dir():
        return "项目目录不存在"

    plan_files = list(project_dir.glob("*-plan.md"))
    progress_files = list(project_dir.glob("*-progress.md"))
    if not plan_files or not progress_files:
        return "缺少 plan.md 或 progress.md"

    old_plan = plan_files[0].read_text(encoding="utf-8")
    old_progress = progress_files[0].read_text(encoding="utf-8")
    plan_filename = plan_files[0].name
    progress_filename = progress_files[0].name

    formatted_steps = format_steps_for_llm(entries)

    new_plan = await _llm_generate(
        ctx,
        ctx.plan_prompt_template,
        old_content=old_plan,
        steps=formatted_steps,
        doc_type="plan",
        project_id=project_id,
    )

    new_progress = await _llm_generate(
        ctx,
        ctx.progress_prompt_template,
        old_content=old_progress,
        steps=formatted_steps,
        doc_type="progress",
        project_id=project_id,
        plan_content=new_plan,
    )

    ideas = extract_ideas(entries)

    plan_semantic = generate_semantic_diff(old_plan, new_plan, "plan")
    progress_semantic = generate_semantic_diff(old_progress, new_progress, "progress")
    plan_unified = generate_unified_diff(
        old_plan, new_plan,
        f"HumanNote/Projects/{project_id}/{plan_filename}",
        f"RobotNote/Projects/{project_id}/{plan_filename}",
    )
    progress_unified = generate_unified_diff(
        old_progress, new_progress,
        f"HumanNote/Projects/{project_id}/{progress_filename}",
        f"RobotNote/Projects/{project_id}/{progress_filename}",
    )

    ctx.project_outputs[project_id] = ProjectOutput(
        project_id=project_id,
        old_plan=old_plan,
        old_progress=old_progress,
        new_plan=new_plan,
        new_progress=new_progress,
        plan_semantic_diff=plan_semantic,
        progress_semantic_diff=progress_semantic,
        plan_unified_diff=plan_unified,
        progress_unified_diff=progress_unified,
        ideas=ideas,
        formatted_steps=formatted_steps,
        plan_filename=plan_filename,
        progress_filename=progress_filename,
    )

    _write_project_outputs(ctx, project_id)

    summary_parts = []
    summary_parts.append("plan 已更新" if plan_unified.strip() else "plan 无变更")
    summary_parts.append(
        "progress 已更新" if progress_unified.strip() else "progress 无变更"
    )
    if ideas:
        total = sum(len(v) for v in ideas.values())
        summary_parts.append(f"idea 已追加 {total} 条")
    return "，".join(summary_parts)


# ---------------------------------------------------------------------------
# review_all_projects：并行审阅所有活跃项目
# ---------------------------------------------------------------------------

async def tool_review_all_projects(ctx: ToolContext, **_: Any) -> str:
    """并行审阅所有已生成的项目，返回审阅结果。"""
    if not ctx.project_outputs:
        return "错误：没有可审阅的项目，请先调用 update_all_projects"

    ctx.review_round += 1
    project_ids = sorted(ctx.project_outputs.keys())

    tasks = [_review_single_project(ctx, pid) for pid in project_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_passed = True
    summary_lines: List[str] = []
    for pid, result in zip(project_ids, results):
        if isinstance(result, Exception):
            logger.error("审阅 %s 失败: %s", pid, result, exc_info=result)
            summary_lines.append(f"- {pid}: 审阅失败（{result}）")
            all_passed = False
        else:
            ctx.review_feedback[pid] = result
            status = "PASS" if result.passed else "NEEDS_REVISION"
            summary_lines.append(f"- {pid}: {status}")
            if not result.passed:
                all_passed = False
                if result.plan_issues and result.plan_issues != "无":
                    summary_lines.append(f"  Plan: {result.plan_issues[:200]}")
                if result.progress_issues and result.progress_issues != "无":
                    summary_lines.append(
                        f"  Progress: {result.progress_issues[:200]}"
                    )

    # 将审阅报告写入 RobotNote
    for pid in project_ids:
        if pid in ctx.review_feedback:
            _write_review_report(ctx, pid)

    verdict = "全部通过" if all_passed else "部分需要人工修订"
    return (
        f"审阅完成（{verdict}），审阅报告已写入 RobotNote：\n"
        + "\n".join(summary_lines)
    )


async def _review_single_project(
    ctx: ToolContext, project_id: str
) -> ReviewResult:
    out = ctx.project_outputs[project_id]

    user_parts = [
        f"# 项目: {project_id}",
        "",
        "## 结构化 Steps（输入源）",
        out.formatted_steps,
        "",
        "## 生成的 Plan",
        out.new_plan,
        "",
        "## 生成的 Progress",
        out.new_progress,
        "",
        "## Plan 变更摘要",
        out.plan_semantic_diff,
        "",
        "## Progress 变更摘要",
        out.progress_semantic_diff,
    ]
    user_msg = "\n\n".join(user_parts)

    request = ConversationRequest(
        system_prompt=ctx.review_prompt_template,
        user_question=user_msg,
    )
    messages = request()

    try:
        response = await ctx.review_llm_client.generate(
            messages=messages,
            stream=False,
            **ctx.review_params,
        )
    except Exception as exc:
        logger.error("审阅 LLM 调用失败（%s）: %s", project_id, exc)
        return ReviewResult(
            project_id=project_id,
            passed=False,
            plan_issues="审阅 LLM 调用失败",
            progress_issues="审阅 LLM 调用失败",
            raw_feedback=str(exc),
        )

    return _parse_review_response(project_id, response)


def _parse_review_response(project_id: str, response: str) -> ReviewResult:
    text = response.strip()
    passed = "VERDICT: PASS" in text.upper() or "VERDICT:PASS" in text.upper()

    plan_issues = "无"
    progress_issues = "无"

    plan_m = re.search(
        r"PLAN_ISSUES:\s*\n(.*?)(?=PROGRESS_ISSUES:|$)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if plan_m:
        plan_issues = plan_m.group(1).strip()

    progress_m = re.search(
        r"PROGRESS_ISSUES:\s*\n(.*?)$",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if progress_m:
        progress_issues = progress_m.group(1).strip()

    if plan_issues == "无" and progress_issues == "无":
        passed = True

    return ReviewResult(
        project_id=project_id,
        passed=passed,
        plan_issues=plan_issues,
        progress_issues=progress_issues,
        raw_feedback=text,
    )


def _write_review_report(ctx: ToolContext, project_id: str) -> None:
    """将审阅报告写入 RobotNote/{project_id}-review.md。"""
    review = ctx.review_feedback[project_id]
    verdict = "PASS" if review.passed else "NEEDS_REVISION"
    today_str = datetime.now().strftime("%Y-%m-%d")

    parts = [
        f"# {project_id} 审阅报告",
        f"> 审阅时间：{today_str}",
        f"> 结论：{verdict}",
    ]

    if review.passed:
        parts.append("\n审阅通过，无需修改。")
    else:
        parts.append(f"\n## Plan 问题\n\n{review.plan_issues}")
        parts.append(f"\n## Progress 问题\n\n{review.progress_issues}")

    out_dir = ctx.robot_root / "Projects" / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{project_id}-review.md").write_text(
        "\n".join(parts) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# revise_all_projects：并行修订所有需要修订的项目（当前未启用，保留代码以备将来使用）
# ---------------------------------------------------------------------------

async def tool_revise_all_projects(ctx: ToolContext, **_: Any) -> str:
    """根据审阅意见并行修订需要修订的项目。"""
    if not ctx.review_feedback:
        return "错误：没有审阅结果，请先调用 review_all_projects"

    to_revise = [
        pid
        for pid, review in ctx.review_feedback.items()
        if not review.passed
    ]
    if not to_revise:
        return "所有项目已通过审阅，无需修订"

    tasks = [_revise_single_project(ctx, pid) for pid in to_revise]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    summary_lines: List[str] = []
    for pid, result in zip(to_revise, results):
        if isinstance(result, Exception):
            logger.error("修订 %s 失败: %s", pid, result, exc_info=result)
            summary_lines.append(f"- {pid}: 修订失败（{result}）")
        else:
            summary_lines.append(f"- {pid}: {result}")

    return f"修订完成（共 {len(to_revise)} 个项目）：\n" + "\n".join(
        summary_lines
    )


async def _revise_single_project(ctx: ToolContext, project_id: str) -> str:
    out = ctx.project_outputs[project_id]
    review = ctx.review_feedback[project_id]

    revised_plan = out.new_plan
    revised_progress = out.new_progress
    revised_any = False

    if review.plan_issues and review.plan_issues != "无":
        revised_plan = await _llm_revise(
            ctx, out.new_plan, review.plan_issues, "plan", project_id
        )
        revised_any = True

    if review.progress_issues and review.progress_issues != "无":
        revised_progress = await _llm_revise(
            ctx,
            out.new_progress,
            review.progress_issues,
            "progress",
            project_id,
            plan_content=revised_plan,
        )
        revised_any = True

    if not revised_any:
        return "无需修订"

    plan_semantic = generate_semantic_diff(out.old_plan, revised_plan, "plan")
    progress_semantic = generate_semantic_diff(
        out.old_progress, revised_progress, "progress"
    )
    plan_unified = generate_unified_diff(
        out.old_plan,
        revised_plan,
        f"HumanNote/Projects/{project_id}/{out.plan_filename}",
        f"RobotNote/Projects/{project_id}/{out.plan_filename}",
    )
    progress_unified = generate_unified_diff(
        out.old_progress,
        revised_progress,
        f"HumanNote/Projects/{project_id}/{out.progress_filename}",
        f"RobotNote/Projects/{project_id}/{out.progress_filename}",
    )

    out.new_plan = revised_plan
    out.new_progress = revised_progress
    out.plan_semantic_diff = plan_semantic
    out.progress_semantic_diff = progress_semantic
    out.plan_unified_diff = plan_unified
    out.progress_unified_diff = progress_unified

    _write_project_outputs(ctx, project_id)

    parts = []
    if review.plan_issues != "无":
        parts.append("plan 已修订")
    if review.progress_issues != "无":
        parts.append("progress 已修订")
    return "，".join(parts) if parts else "已修订"


async def _llm_revise(
    ctx: ToolContext,
    current_content: str,
    issues: str,
    doc_type: str,
    project_id: str,
    plan_content: Optional[str] = None,
) -> str:
    parts = [
        f"# 项目: {project_id}",
        f"# 文档类型: {doc_type}",
        "",
        f"## 当前文档内容\n\n{current_content}",
        f"## 审阅意见\n\n{issues}",
    ]
    if plan_content is not None and doc_type == "progress":
        parts.append(f"## 当前 plan（参考任务 ID 和名称）\n\n{plan_content}")
    user_msg = "\n\n".join(parts)

    request = ConversationRequest(
        system_prompt=ctx.revision_prompt_template,
        user_question=user_msg,
    )
    messages = request()

    try:
        response = await ctx.revision_llm_client.generate(
            messages=messages,
            stream=False,
            **ctx.revision_params,
        )
    except Exception as exc:
        logger.error("修订 LLM 调用失败（%s %s）: %s", doc_type, project_id, exc)
        return current_content

    return _strip_fenced_markdown(response)


# ---------------------------------------------------------------------------
# 公共辅助函数
# ---------------------------------------------------------------------------

def _write_project_outputs(ctx: ToolContext, project_id: str) -> None:
    """将 ProjectOutput 写入 RobotNote 文件。"""
    out = ctx.project_outputs[project_id]
    out_dir = ctx.robot_root / "Projects" / project_id
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / out.plan_filename).write_text(out.new_plan, encoding="utf-8")
    (out_dir / out.progress_filename).write_text(
        out.new_progress, encoding="utf-8"
    )

    plan_diff_name = out.plan_filename.replace("-plan.md", "-plan_diff.md")
    progress_diff_name = out.progress_filename.replace(
        "-progress.md", "-progress_diff.md"
    )

    plan_diff_md = wrap_diff_as_markdown(
        out.plan_semantic_diff,
        out.plan_unified_diff,
        f"{project_id} Plan 变更记录",
        f"HumanNote/Projects/{project_id}/{out.plan_filename}",
        f"RobotNote/Projects/{project_id}/{out.plan_filename}",
    )
    progress_diff_md = wrap_diff_as_markdown(
        out.progress_semantic_diff,
        out.progress_unified_diff,
        f"{project_id} Progress 变更记录",
        f"HumanNote/Projects/{project_id}/{out.progress_filename}",
        f"RobotNote/Projects/{project_id}/{out.progress_filename}",
    )

    (out_dir / plan_diff_name).write_text(plan_diff_md, encoding="utf-8")
    (out_dir / progress_diff_name).write_text(
        progress_diff_md, encoding="utf-8"
    )

    if out.ideas:
        idea_filename = f"{project_id}-idea.md"
        project_dir = ctx.human_root / "Projects" / project_id
        old_idea_path = project_dir / idea_filename
        old_idea = ""
        if old_idea_path.is_file():
            old_idea = old_idea_path.read_text(encoding="utf-8")

        idea_parts: List[str] = (
            [old_idea.rstrip()]
            if old_idea.strip()
            else [f"# {project_id} Ideas\n\n---\n"]
        )
        for d, items in out.ideas.items():
            idea_parts.append(f"\n### {d}")
            for item in items:
                idea_parts.append(f"- {item}")
        (out_dir / idea_filename).write_text(
            "\n".join(idea_parts) + "\n", encoding="utf-8"
        )


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
        parts.append(
            f"## 当前 plan（用于确认任务 ID 和名称）\n\n{plan_content}"
        )
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
    "update_all_projects": tool_update_all_projects,
    "review_all_projects": tool_review_all_projects,
    "save_run_time": tool_save_run_time,
    "finish": tool_finish,
}

TOOL_DESCRIPTIONS = {
    "list_projects": "list_projects() — 列出 HumanNote/Projects 下所有项目目录",
    "read_file": 'read_file(path="相对路径") — 读取 HumanNote 中指定文件内容',
    "read_all_steps": "read_all_steps() — 读取上次运行以来的全部工作步骤",
    "update_all_projects": (
        "update_all_projects() — 代码解析 steps 并并行生成所有活跃项目的"
        " plan/progress/idea 及 diff，写入 RobotNote"
    ),
    "review_all_projects": (
        "review_all_projects() — 并行审阅所有已生成项目的 plan/progress"
        " 质量与完整性，审阅报告写入 RobotNote 供人类参考"
    ),
    "save_run_time": "save_run_time() — 记录本次运行时间戳",
    "finish": 'finish(summary="总结文字") — 宣告本次运行完成',
}
