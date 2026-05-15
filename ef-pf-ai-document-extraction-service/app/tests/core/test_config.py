"""Test cases for the configuration module."""
import pytest
from unittest.mock import patch, MagicMock, mock_open
import json
from app.core.config import load_document_fields, _load_secret_from_gsm, Settings


# =====================================================================
# TESTS FOR load_document_fields
# =====================================================================

def test_load_document_fields_success():
    """Tests loading valid document fields JSON."""
    # Arrange
    test_data = {"field1": "value1", "field2": "value2"}
    test_json = json.dumps(test_data)
    
    with patch("builtins.open", mock_open(read_data=test_json)):
        # Act
        result = load_document_fields("test_path.json")
        
        # Assert
        assert result == test_data


def test_load_document_fields_file_not_found():
    """Tests FileNotFoundError when document fields file doesn't exist."""
    # Arrange
    with patch("builtins.open", side_effect=FileNotFoundError()):
        # Act & Assert
        with pytest.raises(RuntimeError) as exc_info:
            load_document_fields("nonexistent.json")
        assert "CRITICAL: Document fields config file not found" in str(exc_info.value)


def test_load_document_fields_invalid_json():
    """Tests JSONDecodeError when document fields file has invalid JSON."""
    # Arrange
    invalid_json = "{ invalid json content"
    
    with patch("builtins.open", mock_open(read_data=invalid_json)):
        # Act & Assert
        with pytest.raises(RuntimeError) as exc_info:
            load_document_fields("invalid.json")
        assert "CRITICAL: Could not parse document fields config file" in str(exc_info.value)


def test_load_document_fields_empty_json():
    """Tests loading empty JSON object."""
    # Arrange
    test_json = "{}"
    
    with patch("builtins.open", mock_open(read_data=test_json)):
        # Act
        result = load_document_fields("empty.json")
        
        # Assert
        assert result == {}


def test_load_document_fields_complex_structure():
    """Tests loading complex nested JSON structure."""
    # Arrange
    test_data = {
        "fields": {
            "nested": {
                "deep": "value"
            }
        },
        "array": [1, 2, 3]
    }
    test_json = json.dumps(test_data)
    
    with patch("builtins.open", mock_open(read_data=test_json)):
        # Act
        result = load_document_fields("complex.json")
        
        # Assert
        assert result == test_data


# =====================================================================
# TESTS FOR _load_secret_from_gsm
# =====================================================================

def test_load_secret_from_gsm_success():
    """Tests loading secret from Google Secret Manager."""
    # Arrange
    secret_value = "my-secret-key-value"
    mock_response = MagicMock()
    mock_response.payload.data = secret_value.encode("utf-8")
    mock_client = MagicMock()
    mock_client.access_secret_version.return_value = mock_response
    
    with patch("google.cloud.secretmanager.SecretManagerServiceClient", return_value=mock_client):
        # Act
        result = _load_secret_from_gsm("projects/123/secrets/my-secret/versions/latest")
        
        # Assert
        assert result == secret_value
        mock_client.access_secret_version.assert_called_once()


def test_load_secret_from_gsm_with_special_characters():
    """Tests loading secret with special characters."""
    # Arrange
    secret_value = "my-secret-key!@#$%^&*()"
    mock_response = MagicMock()
    mock_response.payload.data = secret_value.encode("utf-8")
    mock_client = MagicMock()
    mock_client.access_secret_version.return_value = mock_response
    
    with patch("google.cloud.secretmanager.SecretManagerServiceClient", return_value=mock_client):
        # Act
        result = _load_secret_from_gsm("projects/123/secrets/special/versions/latest")
        
        # Assert
        assert result == secret_value


def test_load_secret_from_gsm_empty_secret():
    """Tests loading empty secret from Google Secret Manager."""
    # Arrange
    secret_value = ""
    mock_response = MagicMock()
    mock_response.payload.data = secret_value.encode("utf-8")
    mock_client = MagicMock()
    mock_client.access_secret_version.return_value = mock_response
    
    with patch("google.cloud.secretmanager.SecretManagerServiceClient", return_value=mock_client):
        # Act
        result = _load_secret_from_gsm("projects/123/secrets/empty/versions/latest")
        
        # Assert
        assert result == ""


# =====================================================================
# TESTS FOR Settings INITIALIZATION
# =====================================================================

def test_settings_initialization_with_env_vars():
    """Tests Settings initialization with environment variables."""
    # Arrange
    env_vars = {
        "API_BASE_URL": "http://localhost:8000",
        "API_KEY": "test-key-123",
        "PROJECT_ID": "test-project",
        "EXTRACT_SUB_ID": "extract-sub",
        "OUTPUT_TOPIC_ID": "output-topic",
        "DLQ_TOPIC_ID": "dlq-topic",
    }
    
    with patch.dict("os.environ", env_vars):
        with patch("app.core.config.load_document_fields", return_value={}):
            # Act
            settings = Settings()
            
            # Assert
            assert settings.API_BASE_URL == "http://localhost:8000"
            assert settings.API_KEY == "test-key-123"
            assert settings.PROJECT_ID == "test-project"


def test_settings_initialization_with_gsm_secret():
    """Tests Settings initialization loading API_KEY from GSM."""
    # Arrange
    env_vars = {
        "API_BASE_URL": "http://localhost:8000",
        "API_KEY": "projects/123/secrets/api-key/versions/latest",
        "PROJECT_ID": "test-project",
        "EXTRACT_SUB_ID": "extract-sub",
        "OUTPUT_TOPIC_ID": "output-topic",
        "DLQ_TOPIC_ID": "dlq-topic",
    }
    
    mock_response = MagicMock()
    mock_response.payload.data = b"secret-api-key-from-gsm"
    mock_client = MagicMock()
    mock_client.access_secret_version.return_value = mock_response
    
    with patch.dict("os.environ", env_vars):
        with patch("google.cloud.secretmanager.SecretManagerServiceClient", return_value=mock_client):
            with patch("app.core.config.load_document_fields", return_value={}):
                # Act
                settings = Settings()
                
                # Assert
                assert settings.API_KEY == "secret-api-key-from-gsm"


def test_settings_default_values():
    """Tests Settings default values."""
    # Arrange
    env_vars = {
        "API_BASE_URL": "http://localhost:8000",
        "API_KEY": "test-key",
        "PROJECT_ID": "test-project",
        "EXTRACT_SUB_ID": "extract-sub",
        "OUTPUT_TOPIC_ID": "output-topic",
        "DLQ_TOPIC_ID": "dlq-topic",
    }
    
    with patch.dict("os.environ", env_vars):
        with patch("app.core.config.load_document_fields", return_value={}):
            # Act
            settings = Settings()
            
            # Assert
            assert settings.API_MODEL == "gemini-2.5-flash"
            assert settings.API_TIMEOUT == 600
            assert settings.API_MAX_RETRIES == 5
            assert settings.LOG_LEVEL == "INFO"
            assert settings.LOG_FORMAT == "console"


def test_settings_confidence_mapping():
    """Tests Settings confidence score mapping."""
    # Arrange
    env_vars = {
        "API_BASE_URL": "http://localhost:8000",
        "API_KEY": "test-key",
        "PROJECT_ID": "test-project",
        "EXTRACT_SUB_ID": "extract-sub",
        "OUTPUT_TOPIC_ID": "output-topic",
        "DLQ_TOPIC_ID": "dlq-topic",
    }
    
    with patch.dict("os.environ", env_vars):
        with patch("app.core.config.load_document_fields", return_value={}):
            # Act
            settings = Settings()
            
            # Assert
            assert settings.CONFIDENCE_SCORE_MAPPING["HIGH"] == 99
            assert settings.CONFIDENCE_SCORE_MAPPING["MEDIUM"] == 60
            assert settings.CONFIDENCE_SCORE_MAPPING["LOW"] == 25


def test_settings_supported_mime_types():
    """Tests Settings supported MIME types."""
    # Arrange
    env_vars = {
        "API_BASE_URL": "http://localhost:8000",
        "API_KEY": "test-key",
        "PROJECT_ID": "test-project",
        "EXTRACT_SUB_ID": "extract-sub",
        "OUTPUT_TOPIC_ID": "output-topic",
        "DLQ_TOPIC_ID": "dlq-topic",
    }
    
    with patch.dict("os.environ", env_vars):
        with patch("app.core.config.load_document_fields", return_value={}):
            # Act
            settings = Settings()
            
            # Assert
            assert "application/pdf" in settings.SUPPORTED_MIME_TYPES
            assert settings.SUPPORTED_MIME_TYPES["application/pdf"] == "PDF"
            assert settings.SUPPORTED_MIME_TYPES["image/png"] == "PNG"
            assert settings.SUPPORTED_MIME_TYPES["image/jpeg"] == "JPEG"


def test_settings_pubsub_disabled_by_default():
    """Tests that Pub/Sub is disabled by default."""
    # Arrange
    env_vars = {
        "API_BASE_URL": "http://localhost:8000",
        "API_KEY": "test-key",
        "PROJECT_ID": "test-project",
        "EXTRACT_SUB_ID": "extract-sub",
        "OUTPUT_TOPIC_ID": "output-topic",
        "DLQ_TOPIC_ID": "dlq-topic",
    }
    
    with patch.dict("os.environ", env_vars):
        with patch("app.core.config.load_document_fields", return_value={}):
            # Act
            settings = Settings()
            
            # Assert
            assert settings.ENABLE_PUBSUB is False


def test_settings_custom_temp_dir():
    """Tests Settings temporary directory."""
    # Arrange
    env_vars = {
        "API_BASE_URL": "http://localhost:8000",
        "API_KEY": "test-key",
        "PROJECT_ID": "test-project",
        "EXTRACT_SUB_ID": "extract-sub",
        "OUTPUT_TOPIC_ID": "output-topic",
        "DLQ_TOPIC_ID": "dlq-topic",
    }
    
    with patch.dict("os.environ", env_vars):
        with patch("app.core.config.load_document_fields", return_value={}):
            # Act
            settings = Settings()
            
            # Assert
            assert settings.TEMP_DIR == "temp_processing"


def test_settings_document_fields_path():
    """Tests Settings document fields path."""
    # Arrange
    env_vars = {
        "API_BASE_URL": "http://localhost:8000",
        "API_KEY": "test-key",
        "PROJECT_ID": "test-project",
        "EXTRACT_SUB_ID": "extract-sub",
        "OUTPUT_TOPIC_ID": "output-topic",
        "DLQ_TOPIC_ID": "dlq-topic",
    }
    
    with patch.dict("os.environ", env_vars):
        with patch("app.core.config.load_document_fields", return_value={}):
            # Act
            settings = Settings()
            
            # Assert
            assert settings.DOCUMENT_FIELDS_PATH == "document_fields.json"
            assert settings.DOCUMENT_FIELDS == {}
