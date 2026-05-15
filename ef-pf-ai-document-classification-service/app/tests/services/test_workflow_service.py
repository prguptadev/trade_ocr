import pytest
from unittest.mock import patch, MagicMock, call

from app.services.workflow_service import WorkflowService
from app.schemas import ProcessFolderRequest, ClassifiedDocumentsResponse, ClassifiedDocument
from app.services.ai_provider_interface import AIProviderInterface


@pytest.fixture
def mock_ai_provider():
    """Fixture for a mocked AIProviderInterface."""
    return MagicMock(spec=AIProviderInterface)


@pytest.fixture
def mock_doc_processor():
    """Fixture to mock the DocumentProcessor and its methods."""
    with patch("app.services.workflow_service.DocumentProcessor") as mock:
        mock_instance = mock.return_value
        # Mock the return value for the few-shot example preprocessing in __init__
        mock_instance.preprocess_folder.return_value = [
            {
                "filename": "crl_example.pdf",
                "images": [{"mime_type": "image/png", "data": "few_shot_base64_data"}]
            }
        ]
        yield mock_instance


@pytest.fixture
def mock_settings():
    """Fixture to mock the application settings."""
    with patch("app.services.workflow_service.settings") as mock:
        mock.EXAMPLE_FOLDER = "fake_example_folder"
        yield mock


def test_workflow_service_initialization(mock_ai_provider, mock_doc_processor, mock_settings):
    """
    Tests that the WorkflowService initializes correctly, including preprocessing
    the few-shot examples.
    """
    # Act
    service = WorkflowService(ai_provider=mock_ai_provider)

    # Assert
    assert service.ai_provider == mock_ai_provider
    assert service.doc_processor == mock_doc_processor

    # Verify that few-shot preprocessing was called
    mock_doc_processor.preprocess_folder.assert_called_once()
    call_args = mock_doc_processor.preprocess_folder.call_args[0][0]
    assert mock_settings.EXAMPLE_FOLDER in call_args

    # Verify that the few-shot input parts were created
    assert hasattr(service, 'fs_df_input_parts')
    assert len(service.fs_df_input_parts) > 0
    assert any("few_shot_base64_data" in part.get("image_url", "") for part in service.fs_df_input_parts)


def test_process_folder_no_files_found(mock_ai_provider, mock_doc_processor, mock_settings):
    """
    Tests the behavior when no processable files are found in the folder.
    """
    # Arrange
    mock_doc_processor.preprocess_folder.side_effect = [
        # First call in __init__
        [{"filename": "crl_example.pdf", "images": [{"mime_type": "image/png", "data": "few_shot_base64_data"}]}],
        # Second call in process_folder returns empty
        []
    ]
    service = WorkflowService(ai_provider=mock_ai_provider)
    request = ProcessFolderRequest(folder_path="/empty/folder", srn_no="SRN_EMPTY")

    # Act
    response = service.process_folder(request, job_id="job_empty")

    # Assert
    assert response.job_id == "job_empty"
    assert response.documents == []
    assert response.processing_metadata["notes"] == "No files were found to process."
    # Ensure AI provider was not called
    mock_ai_provider.cluster_classify_and_sequence.assert_not_called()


def test_process_folder_success(mock_ai_provider, mock_doc_processor, mock_settings):
    """
    Tests the full successful workflow of processing a folder with documents.
    """
    # Arrange
    # --- Mock preprocessed output ---
    preprocessed_data = [
        {
            "filename": "doc1.pdf",
            "images": [{"mime_type": "image/png", "data": "doc1_base64_data"}]
        },
        {
            "filename": "doc2.jpg",
            "images": [{"mime_type": "image/jpeg", "data": "doc2_base64_data"}]
        }
    ]
    mock_doc_processor.preprocess_folder.side_effect = [
        # First call in __init__
        [{"filename": "crl_example.pdf", "images": [{"mime_type": "image/png", "data": "few_shot_base64_data"}]}],
        # Second call in process_folder
        preprocessed_data
    ]

    # Create a valid request object to be used in mock responses
    mock_request_obj = ProcessFolderRequest(
        folder_path="/path/to/docs", 
        srn_no="SRN123"
    )

    # --- Mock AI responses ---
    classification_response = ClassifiedDocumentsResponse(
        request=mock_request_obj, job_id="job123",
        documents=[
            ClassifiedDocument(document_id="doc_A", document_type="CRL", pages=["doc1.pdf"], confidence_score=99.0),
            ClassifiedDocument(document_id="doc_B", document_type="INVOICE", pages=["doc2.jpg"], confidence_score=99.0)
        ],
        processing_metadata={"ai_call_latency_ms": 1000, "token_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "prompt_tokens_details": {"text_tokens": 10}}}
    )
    
    sequencing_response = ClassifiedDocumentsResponse(
        request=mock_request_obj, job_id="job123",
        documents=[
            ClassifiedDocument(document_id="doc_A", document_type="CRL", pages=["doc1.pdf"], confidence_score=99.0),
            ClassifiedDocument(document_id="doc_B", document_type="INVOICE", pages=["doc2.jpg"], confidence_score=99.0)
        ],
        processing_metadata={"ai_call_latency_ms": 1500, "token_usage": {"prompt_tokens": 200, "completion_tokens": 75, "total_tokens": 275, "prompt_tokens_details": {"text_tokens": 20}}}
    )

    mock_ai_provider.cluster_classify_and_sequence.side_effect = [
        classification_response,
        sequencing_response
    ]

    service = WorkflowService(ai_provider=mock_ai_provider)
    request = mock_request_obj # Use the same object for the actual call

    # Act
    final_response = service.process_folder(request, job_id="job123")

    # Assert
    # --- Verify final response ---
    assert final_response.job_id == "job123"
    # The final documents should come from the *sequencing* response
    assert final_response.documents == sequencing_response.documents

    # --- Verify aggregated metadata ---
    meta = final_response.processing_metadata
    assert meta["ai_call_latency_ms"] == 1000 + 1500
    tokens = meta["token_usage"]
    assert tokens["prompt_tokens"] == 100 + 200
    assert tokens["completion_tokens"] == 50 + 75
    assert tokens["total_tokens"] == 150 + 275
    assert tokens["prompt_tokens_details"]["text_tokens"] == 10 + 20

    # --- Verify calls to dependencies ---
    assert mock_doc_processor.preprocess_folder.call_count == 2
    # Check the call to process the actual request folder
    assert mock_doc_processor.preprocess_folder.call_args_list[1] == call("/path/to/docs")

    assert mock_ai_provider.cluster_classify_and_sequence.call_count == 2
    
    # --- Verify arguments of the first AI call (classification) ---
    classification_call_args = mock_ai_provider.cluster_classify_and_sequence.call_args_list[0].kwargs
    assert classification_call_args['job_id'] == "job123"
    # Check that image data is in the prompt parts
    image_parts_1 = classification_call_args['image_parts']
    assert any("doc1_base64_data" in part.get("image_url", "") for part in image_parts_1)
    assert any("doc2_base64_data" in part.get("image_url", "") for part in image_parts_1)
    assert any("few_shot_base64_data" in part.get("image_url", "") for part in image_parts_1)

    # --- Verify arguments of the second AI call (sequencing) ---
    sequencing_call_args = mock_ai_provider.cluster_classify_and_sequence.call_args_list[1].kwargs
    assert sequencing_call_args['job_id'] == "job123"
    # Check that the prompt parts for the second call are built correctly
    image_parts_2 = sequencing_call_args['image_parts']
    assert any('"document_type": "CRL"' in part.get("text", "") for part in image_parts_2)
    assert any('"page_image_filename": "doc1.pdf"' in part.get("text", "") for part in image_parts_2)
    assert any("doc1_base64_data" in part.get("image_url", "") for part in image_parts_2)
    assert any("doc2_base64_data" in part.get("image_url", "") for part in image_parts_2)
