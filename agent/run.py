"""司理理 Agent CLI 入口。

用法：
    # 核心功能：自主分析更新（全部项目）
    PYTHONPATH=. python agent/run.py --run

    # 限定单项目 + 指定人员 steps
    PYTHONPATH=. python agent/run.py --run --project 001-问津 --person 刘玮康

    # 归档（人类审阅完成后）
    PYTHONPATH=. python agent/run.py --archive 001-问津
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

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

def cmd_archive(project_id: str) -> None:
    human_root = get_humannote_root()
    robot_root = _robot_root()
    project_dir = human_root / "Projects" / project_id
    robot_project_dir = robot_root / "Projects" / project_id

    if not project_dir.is_dir():
        print(f"错误：项目目录不存在 - {project_dir}")
        sys.exit(1)
    if not robot_project_dir.is_dir():
        print(f"错误：RobotNote 中无该项目输出 - {robot_project_dir}")
        sys.exit(1)

    history_dir = project_dir / "history"
    history_dir.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    archived = []
    for suffix in ("-plan.md", "-progress.md"):
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
        print(f"项目 {project_id} 归档完成：")
        print("\n".join(archived))
    else:
        print(f"项目 {project_id} 无可归档文件")


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
        "--archive", metavar="PROJECT_ID",
        help="归档指定项目（如 001-问津）：旧版→history，RobotNote→HumanNote",
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
    elif args.archive:
        cmd_archive(args.archive)


if __name__ == "__main__":
    main()
