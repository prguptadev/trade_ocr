import logging
from typing import Dict
from google.cloud import secretmanager
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_secret_from_gsm(resource_name: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=resource_name)
    return response.payload.data.decode("utf-8")


class Settings(BaseSettings):
    """
    Manages application configuration using environment variables.
    """
    model_config = SettingsConfigDict(
        env_file=".env.dev",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    AI_PROVIDER: str = Field(default="openai", description="The AI provider to use ('openai' or 'vertexai').")
    OPENAI_API_KEY: str = Field(..., description="OpenAI API Key or GSM resource path.")
    OPENAI_BASE_URL: str = Field(..., description="OpenAI Base URL.")

    # Model & Generation Parameters
    MODEL_NAME: str = "gemini-2.5-flash"
    TEMPERATURE: float = 0.01
    TOP_P: float = 0.97
    REASONING_EFFORT: str = "disable"
    MAX_COMPLETION_TOKENS: int = 32768

    # Document Preprocessing Settings
    TARGET_DPI: int = 200
    DEFAULT_DPI: int = 72
    SMALL_ANGLE_THRESHOLD: float = 2.0
    DEFAULT_IMAGE_FORMAT: str = "png"
    SHARPEN_CONTRAST_ALPHA: float = 1.25
    SHARPEN_CONTRAST_BETA: float = 0.0

    # Example data
    EXAMPLE_FOLDER: str = "004BC09243030057"

    # Confidence Metrics
    CONFIDENCE_SCORE_MAPPING: Dict[str, float] = {
        "HIGH": 99, "MEDIUM": 60, "LOW": 25,
        "high": 99, "medium": 60, "low": 25,
        "High": 99, "Medium": 60, "Low": 25,
    }
    DEFAULT_CONFIDENCE: float = 0.0

    # Logging Configuration
    LOGGING_DESTINATION: str = Field(default="CONSOLE", description="Logging destination: 'CONSOLE' or 'FILE'.")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level.")
    LOG_FILE_PATH: str = Field(default="logs/document_processor.log", description="Path to the log file.")
    LOG_ROTATION_MAX_BYTES: int = Field(default=10485760, description="Max log file size in bytes (10MB).")
    LOG_ROTATION_BACKUP_COUNT: int = Field(default=5, description="Number of backup log files.")

    # Pub/Sub Configuration
    PROJECT_ID: str = Field(..., env="PROJECT_ID")
    CLASSIFY_SUB_ID: str = Field(..., env="CLASSIFY_SUB_ID")
    OUTPUT_TOPIC_ID: str = Field(..., env="OUTPUT_TOPIC_ID")
    DLQ_TOPIC_ID: str = Field(..., env="DLQ_TOPIC_ID")
    ENABLE_PUBSUB: bool = Field(default=False, env="ENABLE_PUBSUB")

    def __init__(self, **data):
        super().__init__(**data)

        # Setup logger using config
        log_level = getattr(self, 'LOG_LEVEL', 'INFO')
        logging.basicConfig(level=log_level)
        logger = logging.getLogger(__name__)

        # If OPENAI_API_KEY looks like a GSM secret resource path, load the actual key from Google Secret Manager
        if self.OPENAI_API_KEY.startswith("projects/"):
            original_key = self.OPENAI_API_KEY
            self.OPENAI_API_KEY = _load_secret_from_gsm(self.OPENAI_API_KEY)
            logger.info(f"Loaded OPENAI_API_KEY from Google Secret Manager (path: {original_key[:30]}...)")
        else:
            logger.info("Loaded OPENAI_API_KEY directly from environment variables")


# Single instance
settings = Settings()
