"""
HumanNote（及可配置根目录）只读策略：程序侧禁止向该目录树内写入。

- 环境变量 ``HUMANNOTE_ROOT``：笔记根目录；未设置时默认为本仓库根下的 ``HumanNote/``。
- 所有会修改 HumanNote 内文件的代码应通过 ``assert_can_write`` / ``safe_open`` 统一校验。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import IO, Any, Optional, Union

from base_structure.utils.exceptions import ReadOnlyPathError

# base_structure/utils/readonly_fs.py -> parents[2] = 仓库根（Flowus）
_DEFAULT_HUMANNOTE_REL = Path(__file__).resolve().parents[2] / "HumanNote"


def _path_is_relative_to(path: Path, other: Path) -> bool:
    """兼容 Python 3.8：3.9+ 可用 Path.is_relative_to。"""
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def get_humannote_root() -> Path:
    """
    解析笔记根目录（绝对路径）。

    优先读取环境变量 ``HUMANNOTE_ROOT``；否则使用 ``<仓库根>/HumanNote``。
    """
    raw = os.getenv("HUMANNOTE_ROOT", "").strip()
    if raw:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
        else:
            root = root.resolve()
    else:
        root = _DEFAULT_HUMANNOTE_REL.resolve()
    return root


def assert_can_read(path: Union[str, Path]) -> None:
    """
    读取 HumanNote 内文件是允许的；此函数预留为统一入口（可扩展审计/日志）。
    当前不抛错。
    """
    _ = Path(path)


def assert_can_write(path: Union[str, Path]) -> None:
    """
    若解析后的路径落在 HumanNote 根目录下，则禁止写入。
    用于 ``open(..., 'w')``、``Path.write_text``、创建目录等写操作前的检查。
    """
    p = Path(path)
    try:
        resolved = p.resolve()
    except OSError as e:
        raise ReadOnlyPathError(f"无法解析路径，禁止写入: {p}") from e

    root = get_humannote_root()
    try:
        root_resolved = root.resolve()
    except OSError as e:
        raise ReadOnlyPathError(f"无法解析 HumanNote 根目录: {root}") from e

    if _path_is_relative_to(resolved, root_resolved):
        raise ReadOnlyPathError(
            f"禁止写入只读笔记目录内路径: {resolved}（根: {root_resolved}）"
        )


def safe_open(
    file: Union[str, Path],
    mode: str = "r",
    buffering: int = -1,
    encoding: Optional[str] = None,
    errors: Optional[str] = None,
    newline: Optional[str] = None,
    closefd: bool = True,
    opener: Optional[Any] = None,
) -> IO[Any]:
    """
    与内置 ``open`` 行为一致；若 ``mode`` 含写入/追加/创建语义，则先 ``assert_can_write``。

    写入相关字符：``w`` ``a`` ``x`` ``+``（与 ``r+`` ``w+`` 等组合）。
    纯 ``"r"`` 或 ``"rb"`` 不拦截。
    """
    m = mode.lower()
    if any(c in m for c in ("w", "a", "x", "+")):
        assert_can_write(file)
    return open(  # noqa: SIM115 — 故意返回需调用方关闭的文件对象
        file,
        mode,
        buffering=buffering,
        encoding=encoding,
        errors=errors,
        newline=newline,
        closefd=closefd,
        opener=opener,
    )


if __name__ == "__main__":
    import tempfile

    def _main() -> None:
        root = get_humannote_root()
        print(f"HUMANNOTE_ROOT -> {root}")

        # 1) HumanNote 内写入应失败
        inside = root / "__readonly_fs_probe__.tmp"
        try:
            safe_open(inside, "w").close()
            print("FAIL: 应拒绝写入 HumanNote")
            raise SystemExit(1)
        except ReadOnlyPathError as e:
            print(f"OK: HumanNote 写入被拒: {e.message}")

        # 2) 临时目录写入应成功
        with tempfile.TemporaryDirectory() as td:
            ok_path = Path(td) / "ok.txt"
            with safe_open(ok_path, "w") as f:
                f.write("ok")
            assert ok_path.read_text() == "ok"
            print("OK: 临时目录写入成功")

        print("readonly_fs self-check passed.")

    _main()
