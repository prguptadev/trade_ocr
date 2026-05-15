import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pythonjsonlogger import jsonlogger
from .config import settings

def setup_logging():
    """
    Configures the root logger for the application based on LOGGING_DESTINATION.
    - Logs to a rotating file in JSON format if 'FILE'.
    - Logs to the console in JSON format if 'CONSOLE'.
    """
    # Common setup
    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(levelname)s %(name)s %(message)s %(otelTraceID)s %(otelSpanID)s %(otelServiceName)s',
        rename_fields={'asctime':'timestamp'}
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.LOG_LEVEL)

    # Clear existing handlers to avoid duplicates from uvicorn
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Conditional handler configuration
    destination = settings.LOGGING_DESTINATION.upper()
    if destination == 'FILE':
        # Ensure log directory exists
        log_dir = Path(settings.LOG_FILE_PATH).parent
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create file handler with rotation
        file_handler = RotatingFileHandler(
            settings.LOG_FILE_PATH,
            maxBytes=settings.LOG_ROTATION_MAX_BYTES,
            backupCount=settings.LOG_ROTATION_BACKUP_COUNT
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        log_message = f"Logging configured successfully to file: {settings.LOG_FILE_PATH}"
    else: # Default to CONSOLE
        # Create console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        log_message = "Logging configured successfully to console."

    # Prevent uvicorn's access logs from propagating to the root logger
    logging.getLogger("uvicorn.access").propagate = False
    
    logging.info(log_message)