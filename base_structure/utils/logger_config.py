import logging
import sys
from base_structure.utils.custom_logging import FilterLogging


def setup_logging(level=logging.INFO):
    # 获取根日志记录器
    logger = logging.getLogger()

    # 避免重复添加 handler，这部分逻辑可以保留
    if logger.hasHandlers():
        logger.handlers.clear()

    # 1. 创建一个新的日志格式化程序 (Formatter)
    #    在格式字符串中加入了 [%(request_id)s]
    #    这个 'request_id' 对应我们在 Filter 中添加到 record 对象的属性名
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(request_id)s] - %(module)s:%(lineno)d行： %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # 2. 创建一个处理器 (Handler)，用于将日志输出到控制台
    #    我们使用 sys.stdout 来确保与 uvicorn 等服务器的输出流兼容
    handler = logging.StreamHandler(sys.stdout)

    # 3. 为处理器设置我们新创建的格式化程序
    handler.setFormatter(formatter)

    # 4. 创建我们自定义的过滤器实例
    filter_logging = FilterLogging()

    # 5. 将过滤器添加到处理器上
    #    这样，每一条经过这个处理器的日志都会先被过滤器处理
    handler.addFilter(filter_logging)

    # 6. 将配置好的处理器添加到根日志记录器中
    logger.addHandler(handler)

    # 7. 设置日志记录器的级别
    logger.setLevel(level)


if __name__ == '__main__':
    setup_logging(logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info("Hello, World!")
