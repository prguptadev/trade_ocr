import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.services.openai_provider import OpenAIProvider
from app.schemas import ClassifiedDocumentsResponse, ProcessFolderRequest

# Initialize the TestClient
client = TestClient(app)


@pytest.fixture
def mock_workflow_service():
    """Fixture to mock the WorkflowService."""
    with patch("app.main.WorkflowService") as mock_service:
        # Configure the instance returned by the mock
        mock_instance = mock_service.return_value
        yield mock_instance


def test_health_check():
    """
    Tests the /classify/v1/health endpoint for a successful response.
    """
    response = client.post("/classify/v1/health")
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["message"] == "Welcome to the Document Classification API"
    assert "version" in json_response


def test_process_document_folder_success(mock_workflow_service):
    """
    Tests the /classify/v1/process endpoint for a successful processing request.
    """
    # Arrange
    request_data = {"folder_path": "/fake/folder", "srn_no": "SRN123"}
    
    # Mock the response from workflow_service.process_folder
    mock_response_data = {
        "request": request_data,
        "job_id": "SRN123",
        "documents": [],
        "processing_metadata": {"status": "success"}
    }
    mock_response_obj = ClassifiedDocumentsResponse.model_validate(mock_response_data)
    mock_workflow_service.process_folder.return_value = mock_response_obj

    # Act
    response = client.post("/classify/v1/process", json=request_data)

    # Assert
    assert response.status_code == 200
    assert response.json() == mock_response_data
    
    # Verify that process_folder was called correctly
    # The first argument to process_folder is a ProcessFolderRequest object
    call_args, call_kwargs = mock_workflow_service.process_folder.call_args
    assert isinstance(call_args[0], ProcessFolderRequest)
    assert call_args[0].folder_path == request_data["folder_path"]
    assert call_args[0].srn_no == request_data["srn_no"]
    assert call_args[1] == request_data["srn_no"] # job_id


def test_process_document_folder_file_not_found(mock_workflow_service):
    """
    Tests that a 404 HTTPException is raised when FileNotFoundError occurs.
    """
    # Arrange
    request_data = {"folder_path": "/not/found", "srn_no": "SRN404"}
    error_message = "The specified path was not found: /not/found"
    mock_workflow_service.process_folder.side_effect = FileNotFoundError(error_message)

    # Act
    response = client.post("/classify/v1/process", json=request_data)

    # Assert
    assert response.status_code == 404
    assert response.json()["detail"] == error_message


def test_process_document_folder_internal_server_error(mock_workflow_service):
    """
    Tests that a 500 HTTPException is raised for unexpected errors.
    """
    # Arrange
    request_data = {"folder_path": "/error/folder", "srn_no": "SRN500"}
    mock_workflow_service.process_folder.side_effect = Exception("A critical error occurred")

    # Act
    response = client.post("/classify/v1/process", json=request_data)

    # Assert
    assert response.status_code == 500
    # The job_id is part of the error message, but it's generated in middleware and hard to predict.
    # We can check that the message starts correctly.
    assert "An internal server error occurred" in response.json()["detail"]


def test_get_ai_provider_openai():
    """
    Tests the dependency injector for the 'openai' provider.
    """
    from app.main import get_ai_provider
    
    with patch("app.main.settings") as mock_settings:
        mock_settings.AI_PROVIDER = "openai"
        # Mock the OpenAIProvider's __init__ to avoid actual client initialization
        with patch("app.main.OpenAIProvider", return_value=MagicMock(spec=OpenAIProvider)):
            provider = get_ai_provider()
            assert isinstance(provider, MagicMock)


def test_get_ai_provider_unsupported():
    """
    Tests that the dependency injector raises a ValueError for an unsupported provider.
    """
    from app.main import get_ai_provider

    with patch("app.main.settings") as mock_settings:
        mock_settings.AI_PROVIDER = "unsupported_provider"
        with pytest.raises(ValueError, match="Unsupported AI_PROVIDER configured: unsupported_provider"):
            get_ai_provider()