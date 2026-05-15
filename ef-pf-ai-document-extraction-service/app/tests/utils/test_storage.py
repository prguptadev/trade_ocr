"""Test cases for the storage utility module."""
import pytest
from unittest.mock import patch, MagicMock, mock_open
from google.api_core.exceptions import NotFound
from app.utils.storage import get_file_bytes


# =====================================================================
# TESTS FOR get_file_bytes - LOCAL PATHS
# =====================================================================

def test_get_file_bytes_local_path_success():
    """Tests reading file from local path successfully."""
    # Arrange
    test_content = b"test file content"
    
    with patch("builtins.open", mock_open(read_data=test_content)):
        # Act
        result = get_file_bytes("/path/to/file.txt")
        
        # Assert
        assert result == test_content


def test_get_file_bytes_local_path_binary():
    """Tests reading binary file from local path."""
    # Arrange
    test_content = b"\x89PNG\r\n\x1a\n"
    
    with patch("builtins.open", mock_open(read_data=test_content)):
        # Act
        result = get_file_bytes("/path/to/image.png")
        
        # Assert
        assert result == test_content


def test_get_file_bytes_local_path_large_file():
    """Tests reading large file from local path."""
    # Arrange
    test_content = b"x" * 10000
    
    with patch("builtins.open", mock_open(read_data=test_content)):
        # Act
        result = get_file_bytes("/path/to/large_file.bin")
        
        # Assert
        assert result == test_content
        assert len(result) == 10000


def test_get_file_bytes_local_path_not_found():
    """Tests FileNotFoundError when local file doesn't exist."""
    # Arrange
    with patch("builtins.open", side_effect=FileNotFoundError()):
        # Act & Assert
        with pytest.raises(FileNotFoundError) as exc_info:
            get_file_bytes("/path/to/nonexistent.txt")
        assert "File not found at local path" in str(exc_info.value)


def test_get_file_bytes_local_relative_path():
    """Tests reading file with relative path."""
    # Arrange
    test_content = b"relative path content"
    
    with patch("builtins.open", mock_open(read_data=test_content)):
        # Act
        result = get_file_bytes("./documents/file.txt")
        
        # Assert
        assert result == test_content


def test_get_file_bytes_local_absolute_path():
    """Tests reading file with absolute path."""
    # Arrange
    test_content = b"absolute path content"
    
    with patch("builtins.open", mock_open(read_data=test_content)):
        # Act
        result = get_file_bytes("/absolute/path/to/file.txt")
        
        # Assert
        assert result == test_content


def test_get_file_bytes_local_empty_file():
    """Tests reading empty local file."""
    # Arrange
    test_content = b""
    
    with patch("builtins.open", mock_open(read_data=test_content)):
        # Act
        result = get_file_bytes("/path/to/empty.txt")
        
        # Assert
        assert result == b""


def test_get_file_bytes_local_with_special_characters_in_path():
    """Tests reading file with special characters in path."""
    # Arrange
    test_content = b"special chars content"
    
    with patch("builtins.open", mock_open(read_data=test_content)):
        # Act
        result = get_file_bytes("/path/to/file with spaces & special.txt")
        
        # Assert
        assert result == test_content


# =====================================================================
# TESTS FOR get_file_bytes - GCS PATHS
# =====================================================================

def test_get_file_bytes_gcs_path_success():
    """Tests reading file from GCS successfully."""
    # Arrange
    test_content = b"gcs file content"
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = test_content
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_storage_client = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    
    with patch("google.cloud.storage.Client", return_value=mock_storage_client):
        # Act
        result = get_file_bytes("gs://test-bucket/file.txt")
        
        # Assert
        assert result == test_content
        mock_storage_client.bucket.assert_called_once_with("test-bucket")
        mock_bucket.blob.assert_called_once_with("file.txt")


def test_get_file_bytes_gcs_path_with_nested_path():
    """Tests reading file from GCS with nested path."""
    # Arrange
    test_content = b"nested gcs file"
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = test_content
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_storage_client = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    
    with patch("google.cloud.storage.Client", return_value=mock_storage_client):
        # Act
        result = get_file_bytes("gs://bucket/path/to/nested/file.txt")
        
        # Assert
        assert result == test_content
        mock_bucket.blob.assert_called_once_with("path/to/nested/file.txt")


def test_get_file_bytes_gcs_path_not_found():
    """Tests FileNotFoundError when GCS file doesn't exist."""
    # Arrange
    mock_bucket = MagicMock()
    mock_bucket.blob.side_effect = NotFound("File not found")
    mock_storage_client = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    
    with patch("google.cloud.storage.Client", return_value=mock_storage_client):
        # Act & Assert
        with pytest.raises(FileNotFoundError) as exc_info:
            get_file_bytes("gs://bucket/nonexistent.txt")
        assert "File not found in GCS" in str(exc_info.value)


def test_get_file_bytes_gcs_invalid_uri():
    """Tests ValueError when GCS URI format is invalid."""
    # Arrange
    mock_storage_client = MagicMock()
    
    with patch("google.cloud.storage.Client", return_value=mock_storage_client):
        # Act & Assert
        with pytest.raises(FileNotFoundError) as exc_info:
            get_file_bytes("gs://invalid-path-without-slash")
        assert "File not found in GCS" in str(exc_info.value)


def test_get_file_bytes_gcs_download_error():
    """Tests exception handling when GCS download fails."""
    # Arrange
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.side_effect = Exception("Download failed")
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_storage_client = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    
    with patch("google.cloud.storage.Client", return_value=mock_storage_client):
        # Act & Assert
        with pytest.raises(Exception) as exc_info:
            get_file_bytes("gs://bucket/file.txt")
        assert "Download failed" in str(exc_info.value)


def test_get_file_bytes_gcs_large_file():
    """Tests reading large file from GCS."""
    # Arrange
    test_content = b"x" * 50000
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = test_content
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_storage_client = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    
    with patch("google.cloud.storage.Client", return_value=mock_storage_client):
        # Act
        result = get_file_bytes("gs://bucket/large_file.bin")
        
        # Assert
        assert result == test_content
        assert len(result) == 50000


def test_get_file_bytes_gcs_with_special_bucket_name():
    """Tests reading from GCS bucket with special characters."""
    # Arrange
    test_content = b"special bucket content"
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = test_content
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_storage_client = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    
    with patch("google.cloud.storage.Client", return_value=mock_storage_client):
        # Act
        result = get_file_bytes("gs://my-special-bucket-123/file.txt")
        
        # Assert
        assert result == test_content


def test_get_file_bytes_gcs_path_with_file_extension():
    """Tests reading file from GCS with various extensions."""
    # Arrange
    test_content = b"pdf content"
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = test_content
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_storage_client = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    
    with patch("google.cloud.storage.Client", return_value=mock_storage_client):
        # Act
        result = get_file_bytes("gs://bucket/documents/report.pdf")
        
        # Assert
        assert result == test_content