from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
import logging
import traceback
import json
from base_structure.utils.unified_response import error_response
from base_structure.utils.exceptions import AppBaseException

logger = logging.getLogger(__name__)


def _get_location(exc: Exception) -> str:
    """取 traceback 最后一帧，返回 `文件名:行号(函数名)`"""
    if not exc.__traceback__:
        return "unknown"
    tb = traceback.extract_tb(exc.__traceback__)[-1]
    return f"{tb.filename}:{tb.lineno} ({tb.name})"


def add_exception_handler(app):
    """
    为 FastAPI 应用添加统一的异常处理
    """

    @app.exception_handler(AppBaseException)
    async def handle_app_base_exception(request: Request, exc: AppBaseException):
        """
        捕获所有自定义的业务异常。
        """
        logger.error(
            "自定义业务异常: %s @ %s | %s %s | 信息: %s",
            exc.__class__.__name__,
            _get_location(exc),
            request.method,
            request.url,
            exc.message,
        )
        return error_response(message=exc.message, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """
        处理请求体、查询参数等的校验失败。
        """
        logger.error(
            "请求参数校验失败: %s @ %s | %s %s | 信息: %s",
            exc.__class__.__name__,
            _get_location(exc),
            request.method,
            request.url,
            json.dumps(exc.errors(), ensure_ascii=False, separators=(",", ":"))
        )
        return error_response(message="请求参数校验失败", status_code=422, data=exc.errors())

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        """
        处理ValueError的异常，理论不能有
        """
        logger.error(
            "ValueError: %s @ %s | %s %s| 信息: %s",
            exc.__class__.__name__,
            _get_location(exc),
            request.method,
            request.url,
            str(exc),
        )
        return error_response(message=str(exc), status_code=400)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """
        处理其他所有未捕获的异常
        """
        logger.error(
            "未处理的全局异常: %s @ %s | %s %s",
            exc.__class__.__name__,
            _get_location(exc),
            request.method,
            request.url,
            exc_info=True,  # 完整堆栈继续保留
        )
        return error_response(message="服务内部错误", status_code=500)
