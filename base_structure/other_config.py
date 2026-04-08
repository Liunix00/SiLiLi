from concurrent.futures import ThreadPoolExecutor

################ 并发信号量大小限制 ################
QWEN_VL_MASK_SEM = 10
QWEN_VL_CLASSIFY_SEM = 10
# QWEN_32b_CONCLUSION_SEM = 5

################ 全局线程池（只创建一次）################
MAX_WORKERS = 5
# FastAPI 进程启动时执行一次
executor = ThreadPoolExecutor(
    max_workers=MAX_WORKERS,
    thread_name_prefix="qkb-worker"
)