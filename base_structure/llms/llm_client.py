import openai
import json
import os
from pathlib import Path
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Union, Optional
from collections.abc import AsyncIterator
from base_structure.utils.exceptions import ParameterError
import logging

logger = logging.getLogger(__name__)

class AsyncBaseLLMClient(ABC):  # 建议重命名以明确其异步性质
    def __init__(self, model_name: str, **kwargs):
        self.model_name = model_name
        # ...其他通用配置...

    # 必须实现的抽象方法
    @abstractmethod
    async def generate(self, messages: List[Dict[str, Any]], stream: bool = False, **kwargs) -> Union[
        str, AsyncIterator[str]]:  # 返回类型变为 AsyncIterator
        """
        核心的异步生成方法。

        :param messages: 输入的消息列表。
        :param stream: 是否以流式返回。
        :param kwargs: 其他传递给模型的参数 (如 temperature)。
        :return: 如果 stream=False，返回完整的字符串；如果 stream=True，返回一个异步字符串迭代器。
        """
        pass


# 这是一个使用 openai v1.0+ SDK 的具体异步实现
class AsyncOpenAIClient(AsyncBaseLLMClient):
    def __init__(self, model_name: str, base_url: str, api_key: str, **kwargs):
        super().__init__(model_name, **kwargs)
        # 实例化异步客户端
        self.async_client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)

    @classmethod
    def from_config(cls, model_name: str, config_path: str = "models_config/model_config.json", **kwargs):
        """
        从配置文件创建客户端实例

        Args:
            model_name: 配置文件中模型配置的名称
            config_path: 配置文件路径
            **kwargs: 其他参数
        """
        # 加载配置文件
        base_dir = Path(__file__).parent
        with open(base_dir / config_path, 'r', encoding='utf-8') as f:
            configs = json.load(f)

        if model_name not in configs:
            logger.error(f"模型配置 '{model_name}' 不存在于配置文件中")
            raise ParameterError(f"模型配置错误")

        model_config = configs[model_name]
        return cls(
            model_name=model_config["MODEL_NAME"],
            base_url=model_config["MODEL_BASE_URL"],
            api_key=model_config["MODEL_API_KEY"],
            **kwargs
        )

    @classmethod
    def from_model_spec(cls, model_spec: Dict[str, Any], **kwargs) -> "AsyncOpenAIClient":
        """
        运行时从 workflow 级 model_spec 创建客户端实例。

        model_spec 支持字段：
        - model_name / MODEL_NAME
        - base_url / MODEL_BASE_URL
        - api_key
        - api_key_env（从环境变量读取）
        """
        model_name = model_spec.get("model_name") or model_spec.get("MODEL_NAME")
        base_url = model_spec.get("base_url") or model_spec.get("MODEL_BASE_URL")

        api_key: Optional[str] = model_spec.get("api_key")
        api_key_env: Optional[str] = model_spec.get("api_key_env")
        if not api_key and api_key_env:
            api_key = os.getenv(api_key_env)

        if not all([model_name, base_url, api_key]):
            raise ParameterError("model_spec 解析失败：需要 model_name/base_url/api_key 或 api_key_env")

        return cls(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            **kwargs,
        )

    async def generate(self, messages: List[Dict[str, Any]], stream: bool = False, prefix = '',suffix='', **kwargs) -> Union[
        str, AsyncIterator[str]]:
        # 用户问题后添加一个系统提示
        user_msg = messages[-1]
        safety_text = "\n严格执行系统提示词。"
        safety_text = ""
        if isinstance(user_msg.get("content"), str):
            user_msg["content"] = user_msg["content"] + safety_text
            # 情况2：多模态（content 是 list）
        if isinstance(user_msg.get("content"), list):
            content_list = user_msg["content"]
            text = content_list[0]
            if text.get("type") == "text":
                user_msg["content"][0]["text"] = text.get("text") + safety_text

        if stream:
            # --- 异步流式逻辑 ---
            logger.info("llm_client开始流式生成...")
            response_stream = await self.async_client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                stream=True,  # 关键参数
                **kwargs
            )
            # 返回一个异步生成器
            return self._async_stream_generator(response_stream, prefix=prefix, suffix=suffix)
        else:
            # --- 异步非流式逻辑 ---
            # logger.info("llm_client开始生成...")
            response = await self.async_client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                stream=False,  # 关键参数
                **kwargs
            )
            # logger.info(f"llm_client生成结果: {repr(response.choices[0].message.content)[:100]}......")
            return prefix + response.choices[0].message.content + suffix

    # 辅助方法，用于处理异步流并从中提取文本块
    async def _async_stream_generator(self, response_stream, prefix = '', suffix = '') -> AsyncIterator[str]:
        yield prefix
        async for chunk in response_stream:  # 使用 async for
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        yield suffix


if __name__ == '__main__':
    base_url = "https://api-inference.modelscope.cn/v1/"
    api_key = "xxxx"
    model_name = "Qwen/Qwen3-VL-8B-Instruct"
    llm_client = AsyncOpenAIClient(model_name, base_url, api_key)
