import os
import json
import logging
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Dict, Any, Optional
from google.cloud import secretmanager


def load_document_fields(path: str) -> Dict[str, Any]:
    """Loads the document field definitions from a JSON file."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise RuntimeError(f"CRITICAL: Document fields config file not found at {path}")
    except json.JSONDecodeError:
        raise RuntimeError(f"CRITICAL: Could not parse document fields config file at {path}")


def _load_secret_from_gsm(resource_name: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=resource_name)
    return response.payload.data.decode("utf-8")


class Settings(BaseSettings):
    # API Configuration
    API_BASE_URL: str = Field(..., env="API_BASE_URL")
    API_KEY: str = Field(..., description="OpenAI API Key or GSM resource path.")
    API_MODEL: str = "gemini-2.5-flash"
    API_TIMEOUT: int = Field(default=600, env="API_TIMEOUT")  # 10 minutes
    API_MAX_RETRIES: int = 5
    API_CONCURRENCY_LIMIT: int = 10
    EXPONENTIAL_BACKOFF_FACTOR: float = 2.5
    JSON_CORRECTION_ATTEMPTS: int = 1
    MAX_COMPLETION_TOKENS: int = Field(default=10000, env="MAX_COMPLETION_TOKENS")

    # Confidence Metrics
    CONFIDENCE_SCORE_MAPPING: Dict[str, float] = {
        "HIGH": 99, "High": 99, "high": 99,
        "MEDIUM": 60, "Medium": 60, "medium": 60,
        "LOW": 25, "Low": 25, "low": 25
    }
    
    DEFAULT_CONFIDENCE: float = 0.0

    # Logging Configuration
    LOG_FILE: str = "app_log.log"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "console"

    # File and Directory Settings
    TEMP_DIR: str = "temp_processing"

    # Path to the new external configuration file
    DOCUMENT_FIELDS_PATH: str = "document_fields.json"

    SUPPORTED_MIME_TYPES: dict = {
        "application/pdf": "PDF",
        "image/png": "PNG",
        "image/jpeg": "JPEG",
        "image/jpg": "JPEG",
    }

    # This will be populated after initialization
    DOCUMENT_FIELDS: Dict[str, Any] = {}
    
    # Timeout for LLM call
    LLM_CALL_TIMEOUT: int = Field(default=120, env="LLM_CALL_TIMEOUT")

    # Pub Sub Timeout
    MESSAGE_MAX_AGE_SECONDS: int = Field(default=900, env="MESSAGE_MAX_AGE_SECONDS")  # 15 minutes

    # Pub/Sub Configuration
    PROJECT_ID: str = Field(..., env="PROJECT_ID")
    EXTRACT_SUB_ID: str = Field(..., env="EXTRACT_SUB_ID")
    OUTPUT_TOPIC_ID: str = Field(..., env="OUTPUT_TOPIC_ID")
    DLQ_TOPIC_ID: str = Field(..., env="DLQ_TOPIC_ID")
    ENABLE_PUBSUB: bool = Field(default=False, env="ENABLE_PUBSUB")

    class Config:
        env_file = ".env.dev"
        env_file_encoding = 'utf-8'

    def __init__(self, **data):
        super().__init__(**data)

        # Setup logger using config
        log_level = getattr(self, 'LOG_LEVEL', 'INFO')
        logging.basicConfig(level=log_level)
        logger = logging.getLogger(__name__)

        # Load secret from GSM if API_KEY looks like a GSM resource path
        if self.API_KEY.startswith("projects/"):
            original_key = self.API_KEY
            self.API_KEY = _load_secret_from_gsm(self.API_KEY)
            logger.info(f"Loaded API_KEY from Google Secret Manager (path: {original_key[:30]}...)")
        else:
            logger.info("Loaded API_KEY directly from environment variables")


# Initialize settings from environment variables first
settings = Settings()

# Now, load the external JSON configuration using the path defined in settings
settings.DOCUMENT_FIELDS = load_document_fields(settings.DOCUMENT_FIELDS_PATH)
