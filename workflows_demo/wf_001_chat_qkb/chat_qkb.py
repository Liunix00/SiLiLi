import asyncio
import logging
from pathlib import Path
from typing import List

from base_structure.llms.conversation_system import ConversationRequest
from base_structure.llms.workflow_config import (
    load_llm_client_from_model_config,
    load_workflow_prompt_params,
)
from base_structure.utils.exceptions import LLMClientError
from base_structure.utils.get_file_url import get_img_url

logger = logging.getLogger(__name__)

# 仓库根目录（本 workflow 位于 workflows/wf_001_chat_qkb/）
_WORKFLOW_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _WORKFLOW_DIR.parent.parent
_MODEL_CONFIG_PATH = _REPO_ROOT / "model_config.json"
_PARAM_CONFIG_PATH = _WORKFLOW_DIR / "param_config.json"


async def chat_vllm(
    user_query: str,
    image_paths: List,
    history_message: List = [],
    user_data: str = "",
    prefix: str = "",
    suffix: str = "",
    *,
    preset: str = "default",
    prompt_name: str = "chat1",
) -> str:
    """
    按 preset / prompt_name 从 param_config.json 与引用的 .j2 加载配置，
    现场初始化 AsyncOpenAIClient 后发起对话。
    """
    prompt, params, model_ref = load_workflow_prompt_params(
        _PARAM_CONFIG_PATH,
        preset=preset,
        prompt_name=prompt_name,
    )
    llm_client = load_llm_client_from_model_config(_MODEL_CONFIG_PATH, model_ref)

    if not all([llm_client, prompt, params]):
        logger.error("LLM模型配置解析出错")
        raise LLMClientError("LLM模型配置解析出错")

    images = []
    for image_path in image_paths:
        images.append(await get_img_url(image_path, max_size=2000,use_oss=False))

    request_params = ConversationRequest(
        system_prompt=prompt,
        history_messages=history_message,
        user_question=user_query,
        images=images,
        template_variables={
            "user_data": user_data,
        },
    )
    messages = request_params()

    try:
        response = await llm_client.generate(
            messages=messages,
            stream=False,
            prefix=prefix,
            suffix=suffix,
            **params,
        )
        return response
    except Exception as e:
        logger.error("LLM服务异常：%s", str(e))
        raise LLMClientError("LLM服务异常") from e


if __name__ == "__main__":
    print(
        asyncio.run(
            chat_vllm(
                "用100个字以内回答我，如何获得好心情，在用100字说说图片",
                ["https://ekanzhen.com/pic/pic/pic_1731565862084_1142747.png"],
                [],
                "",
                "",
                preset="default",
                prompt_name="chat1",
            )
        )
    )
