"""司理理 Agent CLI 入口。

用法：
    # 核心功能：自主分析更新（全部项目）
    PYTHONPATH=. python agent/run.py --run

    # 限定单项目 + 指定人员 steps
    PYTHONPATH=. python agent/run.py --run --project 001-问津 --person 刘玮康

    # 归档（人类审阅完成后）
    PYTHONPATH=. python agent/run.py --archive 001-问津

    # 归档全部（仅处理 HumanNote 与 RobotNote 均存在对应目录的项目）
    PYTHONPATH=. python agent/run.py --archive
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

_AGENT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _AGENT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))

from base_structure.llms.workflow_config import (
    load_llm_client_from_model_config,
    load_workflow_prompt_params,
)
from base_structure.utils.readonly_fs import get_humannote_root

from agent.core import SililiAgent
from agent.state_manager import StateManager
from agent.tools import ToolContext, _robot_root

logger = logging.getLogger(__name__)

_MODEL_CONFIG_PATH = _REPO_ROOT / "model_config.json"
_PARAM_CONFIG_PATH = _AGENT_DIR / "param_config.json"


# ---------------------------------------------------------------------------
# --run：启动 Agent
# ---------------------------------------------------------------------------

async def cmd_run(
    project_filter: Optional[str] = None,
    person_filter: Optional[str] = None,
) -> None:
    agent_prompt, agent_params, agent_model_ref = load_workflow_prompt_params(
        _PARAM_CONFIG_PATH, preset="agent", prompt_name="system",
    )
    agent_llm = load_llm_client_from_model_config(_MODEL_CONFIG_PATH, agent_model_ref)

    plan_prompt, gen_params, gen_model_ref = load_workflow_prompt_params(
        _PARAM_CONFIG_PATH, preset="generate", prompt_name="plan_update",
    )
    progress_prompt, _, _ = load_workflow_prompt_params(
        _PARAM_CONFIG_PATH, preset="generate", prompt_name="progress_update",
    )
    gen_llm = load_llm_client_from_model_config(_MODEL_CONFIG_PATH, gen_model_ref)

    human_root = get_humannote_root()
    robot_root = _robot_root()
    state_mgr = StateManager(robot_root)

    ctx = ToolContext(
        repo_root=_REPO_ROOT,
        human_root=human_root,
        robot_root=robot_root,
        gen_llm_client=gen_llm,
        plan_prompt_template=plan_prompt,
        progress_prompt_template=progress_prompt,
        gen_params=gen_params,
        state_mgr=state_mgr,
        project_filter=project_filter,
        person_filter=person_filter,
    )

    agent = SililiAgent(
        agent_llm_client=agent_llm,
        agent_params=agent_params,
        system_prompt_template=agent_prompt,
        tool_ctx=ctx,
    )

    # 开跑前清理 RobotNote/Projects（或按 project_filter 只清对应子目录）
    robot_projects_dir = robot_root / "Projects"
    if project_filter:
        target_clean = robot_projects_dir / project_filter
        if target_clean.is_dir():
            shutil.rmtree(target_clean)
            logger.info("已清理 RobotNote/Projects/%s", project_filter)
    else:
        if robot_projects_dir.is_dir():
            shutil.rmtree(robot_projects_dir)
            logger.info("已清理 RobotNote/Projects/")

    print("=" * 60)
    print("  司理理 Agent 启动")
    print(f"  HumanNote: {human_root}")
    print(f"  RobotNote: {robot_root}")
    last = state_mgr.get_last_run_time()
    print(f"  上次运行: {last.isoformat() if last else '首次运行'}")
    if project_filter:
        print(f"  项目过滤: {project_filter}")
    if person_filter:
        print(f"  人员过滤: {person_filter}")
    print("=" * 60)

    summary = await agent.run()

    print("\n" + "=" * 60)
    print("  运行完成")
    print("=" * 60)
    print(summary)


# ---------------------------------------------------------------------------
# --archive：归档项目
# ---------------------------------------------------------------------------

_ARCHIVE_ALL_SENTINEL = "__ALL__"


def _archive_one_project(
    project_id: str,
    human_root: Path,
    robot_root: Path,
) -> Tuple[bool, List[str]]:
    """将单个人类项目目录与 RobotNote 输出对齐：旧版进 history，Robot 覆盖 Human。

    返回 (成功, 输出行)。成功指 HumanNote 与 RobotNote 下均存在该项目目录。
    """
    project_dir = human_root / "Projects" / project_id
    robot_project_dir = robot_root / "Projects" / project_id

    if not project_dir.is_dir():
        return False, [f"错误：项目目录不存在 - {project_dir}"]
    if not robot_project_dir.is_dir():
        return False, [f"错误：RobotNote 中无该项目输出 - {robot_project_dir}"]

    history_dir = project_dir / "history"
    history_dir.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    archived: List[str] = []
    for suffix in ("-plan.md", "-progress.md", "-idea.md"):
        src_files = list(project_dir.glob(f"*{suffix}"))
        if not src_files:
            continue
        src = src_files[0]
        archive_name = src.stem + f"_{today}" + src.suffix
        dst = history_dir / archive_name
        shutil.copy2(src, dst)
        archived.append(f"  归档: {src.name} → history/{archive_name}")

        robot_file = robot_project_dir / src.name
        if robot_file.is_file():
            shutil.copy2(robot_file, src)
            archived.append(f"  更新: {robot_file.name} → HumanNote (覆盖)")
        else:
            archived.append(f"  跳过: RobotNote 中无 {src.name}")

    if archived:
        lines = [f"项目 {project_id} 归档完成："] + archived
    else:
        lines = [f"项目 {project_id} 无可归档文件"]
    return True, lines


def cmd_archive(project_id: str) -> None:
    human_root = get_humannote_root()
    robot_root = _robot_root()
    ok, lines = _archive_one_project(project_id, human_root, robot_root)
    print("\n".join(lines))
    if not ok:
        sys.exit(1)


def cmd_archive_all() -> None:
    """归档 HumanNote/Projects 下、且在 RobotNote/Projects 中有对应目录的全部项目。"""
    human_root = get_humannote_root()
    robot_root = _robot_root()
    projects_dir = human_root / "Projects"
    if not projects_dir.is_dir():
        print(f"错误：目录不存在 - {projects_dir}")
        sys.exit(1)

    candidate_ids = sorted(
        d.name for d in projects_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
    to_archive = [
        pid for pid in candidate_ids
        if (robot_root / "Projects" / pid).is_dir()
    ]
    skipped = [pid for pid in candidate_ids if pid not in to_archive]

    if not to_archive:
        print("没有可归档的项目（RobotNote/Projects 下无与 HumanNote 对应的项目目录）")
        if skipped:
            print("以下项目在 HumanNote 存在但 RobotNote 无输出，已跳过：")
            for pid in skipped:
                print(f"  - {pid}")
        return

    print(f"批量归档：共 {len(to_archive)} 个项目（RobotNote 有输出）\n")
    any_failure = False
    for i, pid in enumerate(to_archive):
        ok, lines = _archive_one_project(pid, human_root, robot_root)
        if not ok:
            any_failure = True
        print("\n".join(lines))
        if i < len(to_archive) - 1:
            print()

    if skipped:
        print("\n以下项目在 HumanNote 存在但 RobotNote 无输出，未纳入本次归档：")
        for pid in skipped:
            print(f"  - {pid}")

    if any_failure:
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="司理理 Agent — 智能项目管理助手")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run", action="store_true",
        help="启动 Agent 自主分析更新",
    )
    group.add_argument(
        "--archive", nargs="?", const=_ARCHIVE_ALL_SENTINEL, default=None,
        metavar="PROJECT_ID",
        help="归档：旧版→history，RobotNote→HumanNote。省略 PROJECT_ID 时表示归档全部（仅 HumanNote 与 RobotNote 均存在对应目录的项目）",
    )
    parser.add_argument(
        "--project", metavar="PROJECT_ID",
        help="限定只处理指定项目（如 001-问津），与 --run 搭配使用",
    )
    parser.add_argument(
        "--person", metavar="PERSON_NAME",
        help="限定只读取指定人员目录下的 steps（如 刘玮康），与 --run 搭配使用",
    )

    args = parser.parse_args()

    if args.run:
        asyncio.run(cmd_run(
            project_filter=args.project,
            person_filter=args.person,
        ))
    elif args.archive is not None:
        if args.archive == _ARCHIVE_ALL_SENTINEL:
            cmd_archive_all()
        else:
            cmd_archive(args.archive)


if __name__ == "__main__":
    main()
