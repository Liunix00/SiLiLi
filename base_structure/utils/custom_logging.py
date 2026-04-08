import logging
from base_structure.utils.request_context import request_id_var
import re


class FilterLogging(logging.Filter):
    """
    组合过滤器：既添加 request_id 到日志，又过滤 OpenAI/HTTP 请求日志

    功能：
    1. 从 ContextVar 或 request.state 获取 request_id 并添加到日志记录
    2. 过滤掉包含特定模式（如 OpenAI HTTP 请求）的日志
    """

    # 要过滤的日志模式列表，关键字
    _FILTER_PATTERNS = [
        # "_client:1740行",
    ]
    # 正则化过滤
    _FILTER_REGEX_PATTERNS = [
        r'HTTP Request: .*HTTP/1.1 200 OK',  # 匹配 _client:数字行 HTTP/1.1 200 OK
    ]

    def __init__(self, name=""):
        """
        初始化过滤器

        Args:
            name: 过滤器名称
        """
        super().__init__(name)

        # 合并基础过滤模式和额外模式
        self.filter_patterns = list(self._FILTER_PATTERNS)
        self.regex_patterns = [re.compile(p) for p in self._FILTER_REGEX_PATTERNS]

    def filter(self, record):
        """
        过滤日志记录

        Returns:
            bool: True表示保留该日志，False表示过滤掉
        """

        # 根据条件过滤相关日志
        if self._should_filter_message(record.getMessage()):
            return False

        # 注入request_id到日志记录
        self._inject_request_id(record)

        return True

    def _should_filter_message(self, message):
        """
        判断消息是否应该被过滤

        Args:
            message (str): 日志消息

        Returns:
            bool: True表示应该过滤，False表示保留
        """
        # 空消息不处理
        # print(repr(message))
        if not message:
            return False

        # 检查是否包含需要过滤的模式
        for pattern in self.filter_patterns:
            if pattern in message:
                return True

        # 检查正则匹配（更精确）
        for regex in self.regex_patterns:
            if regex.search(message):
                return True

        return False

    def _inject_request_id(self, record):
        """将request_id注入到日志记录中"""
        try:
            # 优先从 ContextVar 拿（线程池里一定有）
            record.request_id = request_id_var.get()
        except LookupError:
            # ContextVar 没值时，再尝试从当前 request.state 拿（异常处理器里能拿到）
            try:
                from starlette.concurrency import get_request
                request = get_request()
                if request and hasattr(request.state, "request_id"):
                    record.request_id = request.state.request_id
                else:
                    record.request_id = "NO-REQ"
            except Exception:
                record.request_id = "UNKNOWN"

if __name__ == '__main__':
    log_message = '2025-12-08 13:44:40 - INFO - [3531ae98519d4d1c8a6784f80e054362] - _client:1740行： HTTP Request: POST http://172.31.0.3:6003/v1/chat/completions "HTTP/1.1 200 OK"'
