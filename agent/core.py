"""司理理 Agent 核心：ReAct 循环（Thought → Action → Observation）。"""
from __future__ import annotations

import ast
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from base_structure.llms.llm_client import AsyncOpenAIClient

from agent.tools import TOOL_DESCRIPTIONS, TOOL_REGISTRY, ToolContext

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 25
_ACTION_RE = re.compile(r"Action\s*:\s*(\w+)\s*\((.*)\)\s*$", re.DOTALL)


class SililiAgent:
    """基于 ReAct 模式的自主 Agent。"""

    def __init__(
        self,
        agent_llm_client: AsyncOpenAIClient,
        agent_params: Dict[str, Any],
        system_prompt_template: str,
        tool_ctx: ToolContext,
    ) -> None:
        self._llm = agent_llm_client
        self._params = agent_params
        self._system_prompt_template = system_prompt_template
        self._ctx = tool_ctx
        self._messages: List[Dict[str, Any]] = []
        self._finished = False
        self._final_summary = ""

    def _build_system_prompt(self) -> str:
        from jinja2 import Template

        tool_desc_lines = "\n".join(
            f"{i+1}. {desc}" for i, desc in enumerate(TOOL_DESCRIPTIONS.values())
        )
        tpl = Template(self._system_prompt_template)
        return tpl.render(
            today=self._ctx.today,
            tool_descriptions=tool_desc_lines,
        )

    async def run(self) -> str:
        system_prompt = self._build_system_prompt()
        self._messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "开始工作。请分析近期步骤并更新相关项目的 plan 和 progress。"},
        ]

        for step in range(1, MAX_ITERATIONS + 1):
            logger.info("=== Agent 第 %d 步 ===", step)

            response_text = await self._call_llm()
            logger.info("Agent 输出:\n%s", response_text)
            self._messages.append({"role": "assistant", "content": response_text})

            tool_name, tool_args = self._parse_action(response_text)
            if tool_name is None:
                correction = (
                    "Observation: 格式错误——未检测到有效的 Action。"
                    "请严格按照格式输出：\n"
                    "Thought: <推理>\n"
                    'Action: tool_name(arg="value")'
                )
                self._messages.append({"role": "user", "content": correction})
                continue

            observation = await self._execute_tool(tool_name, tool_args)
            logger.info("Observation (%s): %s", tool_name, observation[:500])

            if tool_name == "finish":
                self._finished = True
                self._final_summary = observation
                break

            self._messages.append(
                {"role": "user", "content": f"Observation: {observation}"}
            )

        if not self._finished:
            logger.warning("Agent 达到最大迭代次数 (%d) 未完成", MAX_ITERATIONS)
            self._final_summary = "Agent 达到最大步数限制，未正常结束。"

        return self._final_summary

    async def _call_llm(self) -> str:
        try:
            return await self._llm.generate(
                messages=self._messages,
                stream=False,
                **self._params,
            )
        except Exception as exc:
            logger.error("Agent LLM 调用失败: %s", exc)
            raise

    @staticmethod
    def _parse_action(text: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """从 LLM 输出中解析最后一个 Action 调用。

        支持格式：
            Action: tool_name()
            Action: tool_name(key="value", key2="value2")
        """
        lines = text.strip().splitlines()
        action_line = ""
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.lower().startswith("action"):
                action_line = stripped
                break

        if not action_line:
            return None, {}

        m = _ACTION_RE.match(action_line)
        if not m:
            return None, {}

        tool_name = m.group(1)
        args_str = m.group(2).strip()

        if not args_str:
            return tool_name, {}

        parsed_args = _parse_kwargs(args_str)
        return tool_name, parsed_args

    async def _execute_tool(
        self, tool_name: str, tool_args: Dict[str, Any]
    ) -> str:
        func = TOOL_REGISTRY.get(tool_name)
        if func is None:
            return f"错误：未知工具 '{tool_name}'。可用工具: {', '.join(TOOL_REGISTRY)}"
        try:
            return await func(self._ctx, **tool_args)
        except Exception as exc:
            logger.error("工具 %s 执行异常: %s", tool_name, exc, exc_info=True)
            return f"工具执行出错: {exc}"


def _parse_kwargs(args_str: str) -> Dict[str, Any]:
    """解析 key="value", key2="value2" 形式的参数串。"""
    try:
        fake_call = f"_f({args_str})"
        tree = ast.parse(fake_call, mode="eval")
        call_node = tree.body
        result: Dict[str, Any] = {}
        for kw in call_node.keywords:  # type: ignore[attr-defined]
            result[kw.arg] = ast.literal_eval(kw.value)
        return result
    except Exception:
        kv_re = re.compile(r'(\w+)\s*=\s*"((?:[^"\\]|\\.)*)"')
        return {m.group(1): m.group(2) for m in kv_re.finditer(args_str)}
