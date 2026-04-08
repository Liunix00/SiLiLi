from pydantic import BaseModel
from typing import Any, Optional
from fastapi.responses import JSONResponse

class APIResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Any] = None

# 接口处理成功，返回值格式化
def success_response(message: str = "操作成功", data: Any = None) -> JSONResponse:
    """
    返回值格式：
    {"success":true,"message":"操作成功","data":{"name":"张三"}}
    """
    return JSONResponse(
        content=APIResponse(
            success=True,
            message=message,
            data=data
        ).model_dump()
    )

# 用于流式响应的格式化函数
def success_response_stream(chunk_data: Any, message: str = "流式输出结果") -> str:
    """
    将每个流式数据块格式化为统一的JSON字符串，并添加换行符。
    """
    response_model = APIResponse(
        success=True,
        message=message,
        data={"result": chunk_data}
    )
    # 使用 .model_dump_json() 而不是 .model_dump() 来获得JSON字符串
    return response_model.model_dump_json() + "\n"

# 用于流式响应的格式化函数
def success_wake_up_stream(data: Any, message: str = "流式输出结果") -> str:
    """
    将每个流式数据块格式化为统一的JSON字符串，并添加换行符。
    """
    response_model = APIResponse(
        success=True,
        message=message,
        data=data
    )
    # 使用 .model_dump_json() 而不是 .model_dump() 来获得JSON字符串
    return response_model.model_dump_json() + "\n"

# 异常处理返回值的格式化
def error_response(message, status_code: int = 400, data: Any = None) -> JSONResponse:
    """
    返回值格式：
    {"success":false,"message":"操作失败","data":null}
    """
    return JSONResponse(
        status_code=status_code,
        content=APIResponse(
            success=False,
            message=message,
            data=data
        ).model_dump()
    )


if __name__ == '__main__':
    res = success_response(data={'name': '张三'})
    print(res.body.decode('utf-8'))

    res = error_response(message="操作失败")
    print(res.body.decode('utf-8'))
