from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Union
import json
from jinja2 import Template, TemplateError
from pathlib import Path
from base_structure.utils.exceptions import ParameterError
import logging

logger = logging.getLogger(__name__)


# 基础消息单元（统一格式）
@dataclass
class Message:
    """基础消息单元，可表示用户/助手/系统的单条消息"""
    role: str  # "user", "assistant", "system"
    content: str  # 文本内容
    images: Optional[List[str]] = None  # 图片列表["url" / "data:image/jpeg;base64,{base64_data}"]

    def to_dict(self) -> Dict[str, str]:
        """
        生成 OpenAI Vision 格式：
        {
          "role": "user",
          "content": [
             {"type": "text",  "text": "xxx"},
             {"type": "image_url", "image_url": {"url": "data:image/..."}}
          ]
        }
        生成 OpenAI 文本对话格式：
        {
          "role": "user",
          "content": "xxx"
        }
        """
        # 文本对话
        if not self.images:
            return {"role": self.role, "content": self.content}

        # 多模态对话
        content = [{"type": "text", "text": self.content}]
        for img in self.images:
            content.append({
                "type": "image_url",
                "image_url": {"url": img}
            })

        return {"role": self.role, "content": content}


@dataclass
class ConversationRequest:
    """对话请求数据类"""
    system_prompt: str
    user_question: str = ""
    history_messages: List[dict[str, Any]] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    template_variables: Dict[str, Any] = field(default_factory=dict)

    def __call__(self) -> List[Dict[str, Any]]:
        """对话构建器"""
        messages = []

        # 系统消息
        system_content = JinjaPromptManager.prompt_render(
            self.system_prompt,
            self.template_variables
        )
        messages.append(Message("system", system_content).to_dict())

        # 历史消息
        # print(self.history_messages)
        if self.history_messages:
            messages.extend(self.history_messages)

        # 当前用户消息
        user_message = Message(
            role="user",
            content=self.user_question,
            images=self.images
        )
        messages.append(user_message.to_dict())

        return messages


class JinjaPromptManager:
    @staticmethod
    def has_template_variables(template_str: str) -> bool:
        """简单判断模板是否包含变量"""
        return "{{" in template_str and "}}" in template_str

    @staticmethod
    def prompt_render(template_str: str, variables: Dict[str, Any]) -> str:
        """提示词渲染器"""
        if JinjaPromptManager.has_template_variables(template_str) and not variables:
            logger.error(f"警告: 模板包含变量但未提供变量值\n模板: {template_str}")
            raise ParameterError(f"提示词模板错误")

        try:
            template = Template(template_str)
            return template.render(**variables)
        except TemplateError as e:
            logger.error(f"模板渲染失败:{str(e)}")
            raise ParameterError(f"提示词模板错误")


def build_vllm_params(src: Dict[str, Any]) -> Dict[str, Any]:
    """
    分离出 OpenAI 白名单 + vLLM 扩展字段
    """
    openai_allowed = {
        "max_tokens", "temperature", "top_p", "stream", "n", "stop",
        "presence_penalty", "frequency_penalty", "logit_bias", "user",
        "response_format", "tools", "tool_choice", "seed"
    }
    params = {k: v for k, v in src.items() if k in openai_allowed}
    extra = {k: v for k, v in src.items() if k not in openai_allowed}
    if extra:  # 只有需要扩展字段时才带 extra_body
        params["extra_body"] = extra
    return params


def get_prompt_params(config_name: str, prompt_name: Union[str, List[str]],
                      config_path: str = "models_config/system_prompt_config.json"):
    # 加载配置文件
    base_dir = Path(__file__).parent
    with open(base_dir / config_path, 'r', encoding='utf-8') as f:
        configs = json.load(f)

    if config_name not in configs:
        logger.error(f" '{config_name}' 不存在于配置文件中")
        raise ParameterError(f"提示词配置不存在")

    # 统一处理 prompt_name，确保其为列表形式
    if isinstance(prompt_name, str):
        prompt_names = [prompt_name]
    else:
        prompt_names = prompt_name

    # 检查所有 prompt_name 是否存在
    for name in prompt_names:
        if name not in configs[config_name]:
            logger.error(f" '{name}' 不存在于配置文件中")
            raise ParameterError(f"提示词配置不存在")

    # 构建 prompt 字典
    prompt_dict = {}
    for name in prompt_names:
        prompt_content = configs[config_name][name]
        prompt_dict[name] = "".join(prompt_content) if isinstance(prompt_content, list) else prompt_content

    params_dict = configs[config_name]["params"]
    params = build_vllm_params(params_dict)

    return prompt_dict, params


# 使用示例
if __name__ == "__main__":
    # 数据库查询历史消息
    # history = conv_manager.load_history(user_id="user_001")
    history = [
        {"role": "user", "content": "你好，我想优化我的代码"},
        {"role": "assistant", "content": "请告诉我你的代码功能和遇到的问题"}
    ]

    # 获取提示词和模型参数
    prompt_dict, params = get_prompt_params(config_name="meter_config", prompt_name="血脂仪")

    # 构建请求
    request = ConversationRequest(
        system_prompt="我使用的语言是python，我使用的环境是linux,帮我分析问题：",
        # system_prompt="我使用的语言是{{language}}，我使用的环境是{{environment}},帮我分析问题：",
        user_question="我的代码运行很慢，该怎么优化？",
        # history_messages=history,
        # images=["https://example.com/image.png", "data:image/jpeg;base64,{base64_data}"],
        # template_variables={"language": "Python", "environment": {"os": "Linux", "python_version": "3.9"}}
    )
    content = request()
    print(content)

    # 构建对话
    # context = build_messages(request)
    print(json.dumps(content, indent=2, ensure_ascii=False))
