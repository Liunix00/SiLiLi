class AppBaseException(Exception):
    """应用中所有自定义业务异常的基类"""
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class FileNotFoundError(AppBaseException):
    """文件未找到"""
    def __init__(self, message: str = "文件不存在"):
        super().__init__(message, status_code=404)

class FileProcessingError(AppBaseException):
    """文件处理异常"""
    def __init__(self, message: str = "文件处理异常"):
        super().__init__(message, status_code=400)

class LLMClientError(AppBaseException):
    """LLM服务异常"""
    def __init__(self, message: str = "LLM服务异常"):
        super().__init__(message, status_code=500)

class ParameterError(AppBaseException):
    """参数异常"""
    def __init__(self, message: str = "参数异常"):
        super().__init__(message, status_code=400)

class DatabaseError(AppBaseException):
    """数据库操作异常"""
    def __init__(self, message: str = "数据库操作异常"):
        super().__init__(message, status_code=500)


class ReadOnlyPathError(AppBaseException):
    """目标路径位于只读区域（如 HumanNote），禁止程序写入"""

    def __init__(self, message: str = "该路径位于只读目录，禁止写入"):
        super().__init__(message, status_code=403)