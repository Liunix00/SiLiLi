import html
import re


def sanitize_input(user_input: str) -> str:
    """
    安全过滤用户输入，防止XSS等注入攻击
    """
    if not isinstance(user_input, str):
        return user_input  # 如果不是字符串，直接返回

    # Step 1: HTML实体转义（防止<script>执行）
    escaped = html.escape(user_input, quote=True)

    # Step 2: 可选移除危险标签（双保险）
    escaped = re.sub(r"(?i)<script.*?>.*?</script>", "", escaped)
    escaped = re.sub(r"(?i)<.*?on\w+=.*?>", "", escaped)  # 移除带事件的标签

    return escaped