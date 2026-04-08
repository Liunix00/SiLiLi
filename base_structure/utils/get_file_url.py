"""文件下载与图像/PDF 处理。若增加本地落盘/缓存写入，请使用 ``readonly_fs.safe_open``，避免写入 HumanNote 等只读目录。"""
import aiohttp
import aiofiles
from dotenv import load_dotenv
import os
import tos
import logging
import base64
import cv2
import numpy as np
import time
import asyncio
import contextvars
from typing import Optional, List, Union, Tuple
import fitz  # PyMuPDF
from base_structure.utils.exceptions import FileNotFoundError, FileProcessingError
from base_structure.other_config import executor

# --- 配置 ---
logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp')
PDF_EXTENSIONS = ('.pdf',)
FILE_SERVER_ADDRESS = "https://ekanzhen.com/"

# OSS配置
def config_init():
    # 关闭 TosClient 的日志输出
    logging.getLogger('tos').setLevel(logging.CRITICAL)

    # 初始化环境变量
    load_dotenv()

    AK = os.getenv('TOS_ACCESS_KEY')
    SK = os.getenv('TOS_SECRET_KEY')
    ENDPOINT = "tos-cn-beijing.ivolces.com"
    REGION = "cn-beijing"

    # 初始化TosClient，获取OSS客户端
    OSS_CLIENT = tos.TosClientV2(AK, SK, ENDPOINT, REGION)
    return OSS_CLIENT


OSS_CLIENT = config_init()
BUCKET_NAME = "fileset"


async def get_file_from_oss(object_key: str) -> Optional[bytes]:
    """
    从OSS获取文件数据

    Args:
        object_key: OSS中的对象键，例如 "python_file/example_object.txt"

    Returns:
        文件数据的bytes，如果获取失败则返回None
    """
    max_retries = 5 # 次
    base_delay = 5  # 秒
    for attempt in range(max_retries):
        try:
            object_stream = OSS_CLIENT.get_object(BUCKET_NAME, object_key)
            file_data = object_stream.read()
            logger.info(f"成功从OSS获取文件: {object_key}")
            return file_data
        except Exception as e:
            if (attempt+1) < max_retries:
                current_delay = (attempt + 1) * base_delay  # 第1次重试等5秒，第2次等10秒...
                logger.warning(
                    f"从OSS获取文件失败，对象: {object_key}，尝试次数: {attempt + 1}/{max_retries}，5秒后重试...")
                await asyncio.sleep(current_delay)
            else:
                logger.error(f"从OSS获取文件最终失败，对象: {object_key}，已尝试 {max_retries} 次")
                return None


async def download_file(file_path: str, http_prefix: str = FILE_SERVER_ADDRESS, use_oss: bool = True) -> bytes:
    """
    下载文件数据，优先从OSS获取，失败则从原始路径获取

    Args:
        file_path: 文件路径或URL
        oss_object_key: OSS中的对象键

    Returns:
        文件数据的bytes
    """
    filename = os.path.basename(file_path)
    oss_object_key = f"python_file/{filename}"

    # Step 1: 尝试从OSS获取
    file_bytes = await get_file_from_oss(oss_object_key) if use_oss else None

    # Step 2: 如果OSS获取失败，从原始路径获取
    if file_bytes is None:
        logger.info(f"OSS中未找到文件，从原始路径获取: {file_path}")
        # 如果不是HTTP URL或本地路径，则拼接服务器地址
        if not file_path.startswith(('http://', 'https://', '/mnt', '/Users')):
            file_path = http_prefix + file_path
        if file_path.startswith(('http://', 'https://')):
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(file_path, timeout=30.0) as response:
                        if response.status != 200:
                            logger.error(f"远程文件不存在或下载失败，状态码：{response.status}")
                            raise FileNotFoundError(f"远程文件不存在或下载超时：{file_path}")
                        file_bytes = await response.read()
                except asyncio.TimeoutError:
                    logger.error(f"下载远程文件超时（超过30秒）：{file_path}")
                    raise FileNotFoundError(f"下载远程文件超时（超过30秒）：{file_path}")
        else:
            if not os.path.isfile(file_path):
                logger.error(f"本地文件不存在：{file_path}")
                raise FileNotFoundError(f"本地文件不存在：{file_path}")
            async with aiofiles.open(file_path, "rb") as f:
                file_bytes = await f.read()
    else:
        logger.info(f"成功从OSS获取文件: {oss_object_key}")

    return file_bytes


def _process_image_opencv_sync(image_bytes: bytes, max_size: int, original_extension: str) -> str:
    """
    一个使用 OpenCV 的同步图像处理函数。
    它负责解码、缩放和重新编码图像，设计为在独立的线程中运行。

    Args:
        image_bytes: 原始图像的字节数据。
        max_size: 图像最大边的像素限制。
        original_extension: 原始文件的扩展名，用于推断输出格式。

    Returns:
        一个 base64 编码的 data URL。

    Raises:
        FileProcessingError: 如果图像数据无法被处理。
    """
    t3 = time.time()
    try:
        # Step 1: 从内存中的字节数据解码图像
        np_arr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if image is None:
            logger.error("OpenCV 无法解码图像数据，可能图像已损坏。")
            raise FileProcessingError("OpenCV 无法解码图像数据，可能图像已损坏。")

    except Exception as e:
        logger.error(f"使用 OpenCV 解码图像时出错: {e}")
        raise FileProcessingError("图像格式无效或损坏。")
    t4 = time.time()
    logger.info(f"打开图片耗时：{t4 - t3}s")

    # Step 2: 检查尺寸并按需缩放
    height, width = image.shape[:2]
    if max(width, height) > max_size:
        ratio = max_size / max(width, height)
        new_width = int(width * ratio)
        new_height = int(height * ratio)

        resized_image = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)
        logger.info(f"图像已缩放：从 {width}x{height} 到 {new_width}x{new_height}")
    else:
        resized_image = image
        logger.info(f"图像尺寸 {width}x{height} 无需缩放。")
    t5 = time.time()
    logger.info(f"缩放图片耗时：{t5 - t4}s")

    # Step 3: 将处理后的图像编码回字节流
    ext = original_extension.lower()
    if ext in ('.jpg', '.jpeg'):
        mime = 'jpeg'
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 95]
        target_ext = '.jpg'
    elif ext == '.png':
        mime = 'png'
        encode_params = [int(cv2.IMWRITE_PNG_COMPRESSION), 3]
        target_ext = '.png'
    elif ext == '.webp':
        mime = 'webp'
        encode_params = [int(cv2.IMWRITE_WEBP_QUALITY), 95]
        target_ext = '.webp'
    else:
        mime = 'jpeg'
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 95]
        target_ext = '.jpg'

    success, buffer = cv2.imencode(target_ext, resized_image, encode_params)
    if not success:
        logger.error("OpenCV 无法将图像编码为目标格式。")
        raise FileProcessingError("OpenCV 无法将图像编码为目标格式。")

    # Step 4: Base64 编码并格式化为 data URL
    encoded = base64.b64encode(buffer).decode('utf-8')
    image_url = f"data:image/{mime};base64,{encoded}"
    logger.info(f"转为base64耗时：{time.time() - t5}s")

    return image_url


def _process_one_page(args):
    """
    单个页面处理函数：渲染 -> 转Numpy -> OpenCV处理
    """
    pdf_bytes, page_num, dpi, max_size = args

    # 在线程内部打开文档（为了线程安全和避免锁竞争，建议每个线程独立打开）
    # PyMuPDF 打开文档只是读取文件头，非常快，内存开销很小
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_num]

    # 1. 渲染 (耗时操作，现在并行了)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)

    # 2. 直接转 Numpy 数组 (极快，无需 JPEG 编解码)
    # PyMuPDF 的 samples 是 RGB，如果 OpenCV 需要 BGR，后续可能需要转换
    img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
    img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

    # 调用你的处理函数
    # 假设你的 _process_image_opencv_sync 现在支持传入 numpy 数组
    # result = _process_image_opencv_sync(img_bgr, max_size)

    # 如果为了兼容旧代码必须传 bytes：
    success, encoded_img = cv2.imencode('.jpg', img_bgr)
    return _process_image_opencv_sync(encoded_img.tobytes(), max_size, '.jpg')


def _process_pdf_sync_parallel(pdf_bytes: bytes, dpi: int = 150, max_size: int = 2000) -> list:
    try:
        # 仅打开文档获取页数
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        num_pages = doc.page_count
        doc.close()  # 获取完页数就可以关了

        if num_pages == 0:
            return []

        # 准备任务参数
        # 将 pdf_bytes 传入每个线程是安全的（字节串是不可变的）
        tasks = [(pdf_bytes, i, dpi, max_size) for i in range(num_pages)]

        # 真正的并行处理
        # 渲染和压缩都在线程里跑
        results = list(executor.map(_process_one_page, tasks))

        return results


    except Exception as e:
        logger.error(f"处理PDF时出错: {e}")
        raise FileProcessingError(f"PDF处理失败")


def _process_pdf_sync_parallel_(pdf_bytes: bytes, dpi: int = 150, max_size: int = 2000) -> List[str]:
    """
    先提取所有页面，再并行处理
    """
    try:
        # 预先打开文档提取页面信息
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        num_pages = doc.page_count

        if num_pages == 0:
            doc.close()
            return []

        # 预先渲染所有页面为pixmap
        pixmaps = []
        for page_num in range(num_pages):
            page = doc[page_num]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            pixmaps.append(pix.tobytes("jpeg"))

        doc.close()

        # 并行处理图像压缩
        def process_pixmap(img_data):
            return _process_image_opencv_sync(img_data, max_size, '.jpg')

        results = list(executor.map(process_pixmap, pixmaps))

        return results

    except Exception as e:
        logger.error(f"处理PDF时出错: {e}")
        raise FileProcessingError(f"PDF处理失败")


async def get_img_url(image_path: str, max_size: int = 2000, http_prefix: str = FILE_SERVER_ADDRESS,
                      return_raw: bool = False, use_oss: bool = True) -> Union[str, Tuple[str, bytes]]:
    """
    专门处理图像文件，返回data URL

    Args:
        image_path: 图像路径或URL
        max_size: 图片最大边的像素限制
        return_raw: 是否返回原始文件
    Returns:
        data URL字符串
    """
    _, file_extension = os.path.splitext(image_path)
    file_extension = file_extension.lower()

    if file_extension not in IMAGE_EXTENSIONS:
        logger.error(f"不支持的图像类型：{image_path}")
        raise FileProcessingError(f"不支持的图像类型：{image_path}")

    t1 = time.time()

    # 异步获取图像数据
    image_bytes = await download_file(image_path, http_prefix, use_oss)

    t2 = time.time()
    logger.info(f"图像下载耗时：{t2 - t1}s")

    # 处理图像
    loop = asyncio.get_running_loop()
    current_context = contextvars.copy_context()

    try:
        image_url = await loop.run_in_executor(
            executor,
            lambda: current_context.run(_process_image_opencv_sync, image_bytes, max_size, file_extension)
        )
        if return_raw:
            return image_url, image_bytes
        else:
            return image_url
    except Exception as e:
        logger.error(f"处理图片时发生未知错误：{str(e)}")
        raise FileProcessingError("图像缩放时发生异常")


async def get_pdf_url_2img(pdf_url: str, dpi: int = 150, max_size: int = 2000, http_prefix: str = FILE_SERVER_ADDRESS,
                           return_raw: bool = False) -> Union[list[str], Tuple[list[str], bytes]]:
    """
    PDF转base64图片函数

    Args:
        pdf_url: PDF文件URL
        dpi: 转换分辨率
        max_size: 图片最大边的像素限制
        return_raw: 是否返回原是文件
    Returns:
        base64编码的图片data URL列表
    """
    t1 = time.time()

    # 异步获取PDF数据
    pdf_bytes = await download_file(pdf_url, http_prefix)

    t2 = time.time()
    logger.info(f"PDF下载耗时：{t2 - t1}s")

    # 处理PDF
    loop = asyncio.get_running_loop()
    current_context = contextvars.copy_context()

    try:
        image_list = await loop.run_in_executor(
            executor,
            lambda: current_context.run(_process_pdf_sync_parallel, pdf_bytes, dpi, max_size)
        )
        if return_raw:
            return image_list, pdf_bytes
        else:
            return image_list
    except Exception as e:
        logger.error(f"pdf转为图像缩放时发生错误: {e}")
        raise FileProcessingError("pdf转为图像缩放时发生错误")


# --- 测试代码 ---
if __name__ == '__main__':
    async def main():
        # 测试图片
        try:
            # 请确保该路径下有图片文件
            # img_path = '/mnt/datadisk/output_data/save_img_qkb3.1/1.jpg'
            img_path = '/pic/medical/medical_1765274677895_3333629.jpg'
            img_base64 = await get_img_url(img_path, max_size=1024, return_raw=True)
            print(f"图片处理完成，base64长度: {len(img_base64)}")
        except Exception as e:
            print(f"处理图片时发生错误: {e}")

        # 测试PDF
        # try:
        #     # 请确保该路径下有PDF文件
        #     pdf_path = 'https://www.ekanzhen.com//file/3e42273b90f24aee89903ada5da20657.pdf'
        #     # pdf_path = 'https://www.ekanzhen.com//file/3e42273b90f24aee89903ada5da2065.pdf'
        #     pdf_images = await get_pdf_url_2img(pdf_path)
        #     print(f"PDF处理完成，共生成 {len(pdf_images)} 张图片。")
        #     if pdf_images:
        #         print(f"第一张图片的base64长度: {len(pdf_images[0])}")
        # except Exception as e:
        #     print(f"处理PDF时发生错误: {e}")


    asyncio.run(main())
