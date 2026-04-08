from contextvars import ContextVar

# 为 ContextVar 提供一个默认值
# 当在请求上下文之外记录日志时，就会使用这个值
request_id_var: ContextVar[str] = ContextVar("request_id", default="SYSTEM")