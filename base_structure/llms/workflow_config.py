"""
业务侧 workflow 运行时加载：App 级 model_config + workflow 级 param_config + Jinja2 文件引用。

base_structure 不包含任何业务 preset / prompt 内容，仅提供通用解析与客户端创建。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Tuple

from base_structure.llms.conversation_system import build_vllm_params
from base_structure.llms.llm_client import AsyncOpenAIClient
from base_structure.utils.exceptions import ParameterError

logger = logging.getLogger(__name__)


def load_llm_client_from_model_config(
    model_config_path: Path,
    model_name: str,
) -> AsyncOpenAIClient:
    """
    仅从 App 的 ``model_config.json`` 创建 ``AsyncOpenAIClient``。

    :param model_config_path: 例如 ``<repo_root>/model_config.json``
    :param model_name: JSON 顶层 key，与 preset 内 ``model_ref`` 一致
    """
    model_config_path = model_config_path.resolve()
    if not model_config_path.is_file():
        raise ParameterError(f"模型配置文件不存在: {model_config_path}")

    with open(model_config_path, encoding="utf-8") as f:
        model_configs: Dict[str, Any] = json.load(f)

    if model_name not in model_configs:
        logger.error("model_name '%s' 不存在于 %s", model_name, model_config_path)
        raise ParameterError(f"模型配置不存在: {model_name}")

    return AsyncOpenAIClient.from_model_spec(model_configs[model_name])


def load_workflow_prompt_params(
    param_config_path: Path,
    preset: str,
    prompt_name: str,
) -> Tuple[str, Dict[str, Any], str]:
    """
    仅从 workflow 的 ``param_config.json`` 解析 prompt 模板与生成参数，并返回 preset 中的 ``model_ref``。

    :param param_config_path: 例如 ``.../chat_qkb/param_config.json``
    :return: (system_prompt_template, params, model_ref)
    """
    param_config_path = param_config_path.resolve()
    if not param_config_path.is_file():
        raise ParameterError(f"workflow 参数配置不存在: {param_config_path}")

    workflow_dir = param_config_path.parent

    with open(param_config_path, encoding="utf-8") as f:
        param_configs: Dict[str, Any] = json.load(f)

    if preset not in param_configs:
        logger.error("preset '%s' 不存在于 %s", preset, param_config_path)
        raise ParameterError(f"preset 不存在: {preset}")

    preset_cfg = param_configs[preset]
    model_ref = preset_cfg.get("model_ref")
    if not model_ref or not isinstance(model_ref, str):
        raise ParameterError("preset 缺少有效的 model_ref")

    prompts_map = preset_cfg.get("prompts")
    if not isinstance(prompts_map, dict) or prompt_name not in prompts_map:
        raise ParameterError(f"prompt 不存在: {prompt_name}")

    j2_name = prompts_map[prompt_name]
    if not isinstance(j2_name, str) or not j2_name.strip():
        raise ParameterError("prompt 引用无效")

    j2_path = (workflow_dir / j2_name).resolve()
    if not j2_path.is_file():
        raise ParameterError(f"prompt 文件不存在: {j2_path}")

    system_prompt_template = j2_path.read_text(encoding="utf-8")

    raw_params = preset_cfg.get("params") or {}
    if not isinstance(raw_params, dict):
        raise ParameterError("preset.params 必须是对象")
    params = build_vllm_params(raw_params)

    return system_prompt_template, params, model_ref


def load_workflow_llm_resources(
    app_model_config_path: Path,
    workflow_dir: Path,
    preset: str,
    prompt_name: str,
) -> Tuple[AsyncOpenAIClient, str, Dict[str, Any]]:
    """
    组合封装：等价于先 ``load_workflow_prompt_params`` 再 ``load_llm_client_from_model_config``。

    :param app_model_config_path: 例如 ``<repo_root>/model_config.json``
    :param workflow_dir: 某个 workflow 目录，内含 ``param_config.json`` 与引用的 ``*.j2``
    :param preset: param_config 顶层 key（多套参数）
    :param prompt_name: preset 内 ``prompts`` 映射的 key
    :return: (llm_client, system_prompt_template, params)
    """
    app_model_config_path = app_model_config_path.resolve()
    workflow_dir = workflow_dir.resolve()
    param_path = workflow_dir / "param_config.json"

    prompt, params, model_ref = load_workflow_prompt_params(
        param_path, preset=preset, prompt_name=prompt_name
    )
    llm_client = load_llm_client_from_model_config(app_model_config_path, model_ref)
    return llm_client, prompt, params
