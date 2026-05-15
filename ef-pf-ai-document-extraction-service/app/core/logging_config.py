# app/core/logging_config.py
import logging
from logging.config import dictConfig
import sys
from pythonjsonlogger import jsonlogger
from app.core.config import settings

class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter to add the record's name to the log.
    """
    def add_fields(self, log_record, record, message_dict):
        super(CustomJsonFormatter, self).add_fields(log_record, record, message_dict)
        log_record['name'] = record.name

def setup_logging():
    """
    Configures structured, professional logging for the application.
    Supports both human-readable console logging and machine-readable JSON logging.
    """
    log_level = settings.LOG_LEVEL.upper()
    log_format = settings.LOG_FORMAT.lower()

    console_handler = {
        "class": "logging.StreamHandler",
        "stream": sys.stdout,
        "formatter": "console" if log_format == "console" else "json",
    }

    file_handler = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": settings.LOG_FILE,
        "maxBytes": 1024 * 1024 * 10,  # 10 MB
        "backupCount": 10,
        "formatter": "json",
    }
    
    LOGGING_CONFIG = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "json": {
                "()": "app.core.logging_config.CustomJsonFormatter",
                "format": "%(asctime)s %(name)s %(levelname)s %(message)s %(filename)s %(lineno)d %(otelTraceID)s %(otelSpanID)s %(otelServiceName)s %(otelTraceSampled)s",
            },
        },
        "handlers": {
            "console": console_handler,
            "file": file_handler,
        },
        "loggers": {
            # Root logger
            "": {
                "handlers": ["console", "file"],
                "level": log_level,
            },
            # Specific loggers for libraries can be configured here if needed
            "gunicorn.error": {
                "handlers": ["console", "file"],
                "level": "INFO",
                "propagate": False,
            },
            "gunicorn.access": {
                "handlers": ["console", "file"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }
    dictConfig(LOGGING_CONFIG)

log = logging.getLogger(__name__)
