"""last_run 时间戳持久化，存储在 RobotNote/_silili_state/last_run.json。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_STATE_DIR_NAME = "_silili_state"
_STATE_FILE_NAME = "last_run.json"


class StateManager:
    def __init__(self, robot_root: Path) -> None:
        self._state_dir = robot_root / _STATE_DIR_NAME
        self._state_file = self._state_dir / _STATE_FILE_NAME

    def get_last_run_time(self) -> Optional[datetime]:
        if not self._state_file.is_file():
            return None
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            return datetime.fromisoformat(data["last_run"])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("无法解析 last_run 状态文件，视为首次运行: %s", exc)
            return None

    def save_run_time(self, ts: Optional[datetime] = None) -> None:
        ts = ts or datetime.now()
        self._state_dir.mkdir(parents=True, exist_ok=True)
        payload = {"last_run": ts.isoformat(timespec="seconds")}
        self._state_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("已保存运行时间: %s", payload["last_run"])
