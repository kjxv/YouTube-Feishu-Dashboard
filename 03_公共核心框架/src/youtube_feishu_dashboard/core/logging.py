"""公共日志初始化；模块只能通过 logging.getLogger 获取记录器。"""

from __future__ import annotations

import json
import logging
import logging.config
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key in ("task_id", "run_id", "module_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    level: str = "INFO", *, json_output: bool = False, log_file: Path | None = None
) -> None:
    handlers: dict[str, dict[str, Any]] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if json_output else "human",
            "level": level,
        }
    }
    root_handlers = ["console"]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_file),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "json",
            "level": level,
        }
        root_handlers.append("file")
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "human": {"format": "%(asctime)s %(levelname)s %(name)s - %(message)s"},
                "json": {"()": JsonFormatter},
            },
            "handlers": handlers,
            "root": {"level": level, "handlers": root_handlers},
        }
    )
