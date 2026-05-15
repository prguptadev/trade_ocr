"""Test cases for the logging configuration module."""
import pytest
from unittest.mock import patch, MagicMock
from app.core.logging_config import setup_logging, CustomJsonFormatter


# =====================================================================
# SHARED FIXTURES & SETUP
# =====================================================================

@pytest.fixture
def mock_settings():
    """Fixture for mocked settings."""
    settings = MagicMock()
    settings.LOG_LEVEL = "INFO"
    settings.LOG_FORMAT = "console"
    return settings


@pytest.fixture
def mock_dict_config():
    """Fixture for mocked dictConfig."""
    with patch('app.core.logging_config.dictConfig') as mock:
        yield mock


# =====================================================================
# TESTS FOR CustomJsonFormatter
# =====================================================================

def test_custom_json_formatter_adds_name_field():
    """Tests that CustomJsonFormatter adds name field to log record."""
    # Arrange
    formatter = CustomJsonFormatter()
    mock_record = MagicMock()
    mock_record.name = "test.logger"
    log_record = {}
    message_dict = {}
    
    # Act
    formatter.add_fields(log_record, mock_record, message_dict)
    
    # Assert
    assert "name" in log_record
    assert log_record["name"] == "test.logger"


def test_custom_json_formatter_inherits_from_json_formatter():
    """Tests that CustomJsonFormatter properly inherits from JsonFormatter."""
    # Arrange & Act
    formatter = CustomJsonFormatter()
    
    # Assert
    assert hasattr(formatter, 'format')
    assert hasattr(formatter, 'add_fields')


def test_custom_json_formatter_with_different_logger_names():
    """Tests CustomJsonFormatter with various logger names."""
    # Arrange
    formatter = CustomJsonFormatter()
    logger_names = ["root", "app.services", "app.utils.helpers", "third_party.lib"]
    
    # Act & Assert
    for logger_name in logger_names:
        mock_record = MagicMock()
        mock_record.name = logger_name
        log_record = {}
        message_dict = {}
        
        formatter.add_fields(log_record, mock_record, message_dict)
        
        assert log_record["name"] == logger_name


# =====================================================================
# TESTS FOR setup_logging
# =====================================================================

def test_setup_logging_with_console_format(mock_settings, mock_dict_config):
    """Tests setup_logging configures console format correctly."""
    # Arrange
    mock_settings.LOG_LEVEL = "INFO"
    mock_settings.LOG_FORMAT = "console"
    
    with patch('app.core.logging_config.settings', mock_settings):
        # Act
        setup_logging()
        
        # Assert
        mock_dict_config.assert_called_once()
        call_args = mock_dict_config.call_args[0][0]
        assert "version" in call_args
        assert "handlers" in call_args
        assert "loggers" in call_args


def test_setup_logging_with_json_format(mock_settings, mock_dict_config):
    """Tests setup_logging configures JSON format correctly."""
    # Arrange
    mock_settings.LOG_LEVEL = "DEBUG"
    mock_settings.LOG_FORMAT = "json"
    
    with patch('app.core.logging_config.settings', mock_settings):
        # Act
        setup_logging()
        
        # Assert
        mock_dict_config.assert_called_once()
        call_args = mock_dict_config.call_args[0][0]
        assert "formatters" in call_args


def test_setup_logging_with_warning_level(mock_settings, mock_dict_config):
    """Tests setup_logging with WARNING log level."""
    # Arrange
    mock_settings.LOG_LEVEL = "WARNING"
    mock_settings.LOG_FORMAT = "console"
    
    with patch('app.core.logging_config.settings', mock_settings):
        # Act
        setup_logging()
        
        # Assert
        mock_dict_config.assert_called_once()
        call_args = mock_dict_config.call_args[0][0]
        assert call_args["loggers"]["" ]["level"] == "WARNING"


def test_setup_logging_with_error_level(mock_settings, mock_dict_config):
    """Tests setup_logging with ERROR log level."""
    # Arrange
    mock_settings.LOG_LEVEL = "ERROR"
    mock_settings.LOG_FORMAT = "console"
    
    with patch('app.core.logging_config.settings', mock_settings):
        # Act
        setup_logging()
        
        # Assert
        mock_dict_config.assert_called_once()
        call_args = mock_dict_config.call_args[0][0]
        assert call_args["loggers"]["" ]["level"] == "ERROR"


def test_setup_logging_with_debug_level(mock_settings, mock_dict_config):
    """Tests setup_logging with DEBUG log level."""
    # Arrange
    mock_settings.LOG_LEVEL = "DEBUG"
    mock_settings.LOG_FORMAT = "console"
    
    with patch('app.core.logging_config.settings', mock_settings):
        # Act
        setup_logging()
        
        # Assert
        mock_dict_config.assert_called_once()
        call_args = mock_dict_config.call_args[0][0]
        assert call_args["loggers"]["" ]["level"] == "DEBUG"


def test_setup_logging_with_critical_level(mock_settings, mock_dict_config):
    """Tests setup_logging with CRITICAL log level."""
    # Arrange
    mock_settings.LOG_LEVEL = "CRITICAL"
    mock_settings.LOG_FORMAT = "console"
    
    with patch('app.core.logging_config.settings', mock_settings):
        # Act
        setup_logging()
        
        # Assert
        mock_dict_config.assert_called_once()
        call_args = mock_dict_config.call_args[0][0]
        assert call_args["loggers"]["" ]["level"] == "CRITICAL"


def test_setup_logging_includes_console_handler(mock_settings, mock_dict_config):
    """Tests that setup_logging includes console handler."""
    # Arrange
    mock_settings.LOG_LEVEL = "INFO"
    mock_settings.LOG_FORMAT = "console"
    
    with patch('app.core.logging_config.settings', mock_settings):
        # Act
        setup_logging()
        
        # Assert
        mock_dict_config.assert_called_once()
        call_args = mock_dict_config.call_args[0][0]
        assert len(call_args["handlers"]) > 0