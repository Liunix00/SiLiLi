import argparse
import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import List

from base_structure.llms.conversation_system import ConversationRequest
from base_structure.llms.workflow_config import (
    load_llm_client_from_model_config,
    load_workflow_prompt_params,
)
from base_structure.utils.exceptions import LLMClientError, ParameterError
from base_structure.utils.readonly_fs import get_humannote_root

logger = logging.getLogger(__name__)

_WORKFLOW_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _WORKFLOW_DIR.parent.parent
_MODEL_CONFIG_PATH = _REPO_ROOT / "model_config.json"
_PARAM_CONFIG_PATH = _WORKFLOW_DIR / "param_config.json"


def _robot_root() -> Path:
    raw = os.environ.get("ROBOTNOTE_ROOT", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        else:
            p = p.resolve()
        return p
    return (_REPO_ROOT / "RobotNote").resolve()


def _normalize_relative(rel: str) -> Path:
    p = Path(rel.strip())
    if p.is_absolute() or ".." in p.parts:
        raise ParameterError("路径必须为相对路径且不得包含 ..")
    return p


def _strip_fenced_markdown(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*$", t)
    if m:
        return m.group(1).strip()
    return t


async def run_workflow(
    *,
    human_relative_path: str,
    history_message: List = [],
    preset: str = "default",
    prompt_name: str = "polish1",
) -> str:
    """
    将 HumanNote 中指定相对路径的文件复制到 RobotNote 同路径，调用 LLM 润色后写回 RobotNote。

    :param human_relative_path: 相对于 HumanNote 根的文件路径，例如 ``2026.4.1 test.md``
    :return: 润色后的 Markdown 正文（与写入 RobotNote 的内容一致）
    """
    rel = _normalize_relative(human_relative_path)
    human_root = get_humannote_root()
    robot_root = _robot_root()
    src = (human_root / rel).resolve()
    dst = (robot_root / rel).resolve()

    try:
        human_resolved = human_root.resolve()
    except OSError as e:
        raise ParameterError(f"无法解析 HumanNote 根目录: {human_root}") from e

    if not src.is_file():
        raise ParameterError(f"源文件不存在或不是文件: {src}")

    try:
        src.relative_to(human_resolved)
    except ValueError as e:
        raise ParameterError("源路径必须位于 HumanNote 根目录内") from e

    robot_resolved = robot_root.resolve()
    try:
        dst.resolve().relative_to(robot_resolved)
    except ValueError as e:
        raise ParameterError("目标路径必须位于 RobotNote 根目录内") from e

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

    raw_text = src.read_text(encoding="utf-8")

    prompt, params, model_ref = load_workflow_prompt_params(
        _PARAM_CONFIG_PATH,
        preset=preset,
        prompt_name=prompt_name,
    )
    llm_client = load_llm_client_from_model_config(_MODEL_CONFIG_PATH, model_ref)

    if not all([llm_client, prompt, params]):
        logger.error("LLM模型配置解析出错")
        raise LLMClientError("LLM模型配置解析出错")

    request_params = ConversationRequest(
        system_prompt=prompt,
        history_messages=history_message,
        user_question=raw_text,
        images=[],
    )
    messages = request_params()

    try:
        response = await llm_client.generate(
            messages=messages,
            stream=False,
            prefix="",
            suffix="",
            **params,
        )
    except Exception as e:
        logger.error("LLM服务异常：%s", str(e))
        raise LLMClientError("LLM服务异常") from e

    polished = _strip_fenced_markdown(response)
    dst.write_text(polished, encoding="utf-8")
    return polished


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="HumanNote → RobotNote 单文件复制并 LLM 润色")
    parser.add_argument(
        "--file",
        "-f",
        required=True,
        help="相对于 HumanNote 根的文件路径，例如：2026.4.1 test.md",
    )
    args = parser.parse_args()
    out = asyncio.run(run_workflow(human_relative_path=args.file))
    print(out)


if __name__ == "__main__":
    main()
