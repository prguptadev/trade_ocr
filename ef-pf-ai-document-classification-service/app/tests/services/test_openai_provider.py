import pytest
import json
from unittest.mock import patch, MagicMock, call

from app.services.openai_provider import OpenAIProvider
from app.schemas import ProcessFolderRequest, NonExtractedDocumentsWithConfidenceLevel, NonExtractedDocumentWithConfidenceLevel


@pytest.fixture
def mock_settings():
    """Fixture to mock application settings."""
    with patch("app.services.openai_provider.settings") as mock:
        mock.OPENAI_API_KEY = "test-key"
        mock.OPENAI_BASE_URL = "https://api.test.com"
        mock.MODEL_NAME = "test-model"
        mock.TEMPERATURE = 0.1
        mock.TOP_P = 0.9
        mock.REASONING_EFFORT = "disable"
        mock.MAX_COMPLETION_TOKENS = 4096
        mock.CONFIDENCE_SCORE_MAPPING = {"HIGH": 99.0, "MEDIUM": 60.0, "LOW": 25.0}
        mock.DEFAULT_CONFIDENCE = 0.0
        yield mock


@pytest.fixture
def mock_openai_client():
    """Fixture to mock the openai.OpenAI client."""
    with patch("app.services.openai_provider.openai.OpenAI") as mock_client:
        yield mock_client


@pytest.fixture
def mock_httpx_client():
    """Fixture to mock the httpx.Client."""
    with patch("app.services.openai_provider.httpx.Client") as mock_http:
        yield mock_http


def test_openai_provider_initialization_success(mock_settings, mock_openai_client, mock_httpx_client):
    """Tests successful initialization of the OpenAIProvider."""
    # Act
    provider = OpenAIProvider()

    # Assert
    mock_httpx_client.assert_called_once_with(http2=True, verify=False)
    mock_openai_client.assert_called_once_with(
        api_key="test-key",
        base_url="https://api.test.com",
        http_client=mock_httpx_client.return_value
    )
    assert provider.client == mock_openai_client.return_value


def test_openai_provider_initialization_failure(mock_settings, mock_openai_client):
    """Tests that initialization raises an exception if the client fails."""
    # Arrange
    mock_openai_client.side_effect = Exception("Connection failed")

    # Act & Assert
    with pytest.raises(Exception, match="Connection failed"):
        OpenAIProvider()


@pytest.fixture
def provider(mock_settings, mock_openai_client, mock_httpx_client):
    """Fixture to get an initialized OpenAIProvider instance."""
    return OpenAIProvider()


@pytest.fixture
def mock_process_request():
    """Fixture for a sample ProcessFolderRequest."""
    return ProcessFolderRequest(folder_path="/fake/path", srn_no="SRN123")


def create_mock_llm_response(parsed_obj=None, content_str=None, usage=None):
    """Helper to create a mock LLM response object."""
    mock_response = MagicMock()
    mock_message = MagicMock()

    if parsed_obj:
        mock_message.parsed = parsed_obj
    else:
        mock_message.parsed = None

    mock_message.content = content_str
    mock_response.choices = [MagicMock(message=mock_message)]

    if usage:
        mock_usage = MagicMock()
        mock_usage.to_dict.return_value = usage
        mock_response.usage = mock_usage
    else:
        mock_response.usage = None

    return mock_response


def test_cluster_classify_and_sequence_success_with_parsed(provider, mock_process_request):
    """Tests the success path where the LLM response is already parsed."""
    # Arrange
    parsed_data = NonExtractedDocumentsWithConfidenceLevel(
        documents=[
            NonExtractedDocumentWithConfidenceLevel(
                document_id="doc1", document_type="CRL", confidence_level="HIGH", pages=["page1.png"]
            )
        ]
    )
    usage_data = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    mock_response = create_mock_llm_response(parsed_obj=parsed_data, usage=usage_data)
    provider.client.chat.completions.parse.return_value = mock_response

    # Act
    result = provider.cluster_classify_and_sequence(
        request=mock_process_request, image_parts=[], prompt="test prompt", job_id="job1"
    )

    # Assert
    assert result.job_id == "job1"
    assert len(result.documents) == 1
    assert result.documents[0].document_type == "CRL"
    assert result.documents[0].confidence_score == 99.0
    assert result.processing_metadata["token_usage"] == usage_data
    assert "ai_call_latency_ms" in result.processing_metadata


def test_cluster_classify_and_sequence_success_with_fallback_parsing(provider, mock_process_request):
    """Tests the success path using fallback JSON parsing from a string."""
    # Arrange
    response_dict = {
        "documents": [{
            "document_id": "doc2", "document_type": "INVOICE", "confidence_level": "MEDIUM", "pages": ["page2.png"]
        }]
    }
    # Simulate a response wrapped in markdown code fences
    content_str = f"```json\n{json.dumps(response_dict)}\n```"
    mock_response = create_mock_llm_response(content_str=content_str)
    provider.client.chat.completions.parse.return_value = mock_response

    # Act
    result = provider.cluster_classify_and_sequence(
        request=mock_process_request, image_parts=[], prompt="test prompt", job_id="job2"
    )

    # Assert
    assert len(result.documents) == 1
    assert result.documents[0].document_type == "INVOICE"
    assert result.documents[0].confidence_score == 60.0


@patch("app.services.openai_provider.time.sleep")
def test_cluster_classify_and_sequence_retry_on_api_error(mock_sleep, provider, mock_process_request):
    """Tests the retry logic for transient API errors."""
    # Arrange
    # Simulate an API error on the first call, then a success
    from openai import RateLimitError
    parsed_data = NonExtractedDocumentsWithConfidenceLevel(documents=[])
    mock_success_response = create_mock_llm_response(parsed_obj=parsed_data)

    provider.client.chat.completions.parse.side_effect = [
        RateLimitError("Rate limit exceeded", response=MagicMock(), body=None),
        mock_success_response
    ]

    # Act
    provider.cluster_classify_and_sequence(
        request=mock_process_request, image_parts=[], prompt="test prompt", job_id="job3"
    )

    # Assert
    assert provider.client.chat.completions.parse.call_count == 2
    mock_sleep.assert_called_once()


def test_cluster_classify_and_sequence_json_parse_error(provider, mock_process_request):
    """Tests that a ValueError is raised for malformed JSON."""
    # Arrange
    mock_response = create_mock_llm_response(content_str="this is not json")
    provider.client.chat.completions.parse.return_value = mock_response

    # Act & Assert
    with pytest.raises(ValueError, match="Could not parse a valid JSON object"):
        provider.cluster_classify_and_sequence(
            request=mock_process_request, image_parts=[], prompt="test prompt", job_id="job4"
        )

def test_cluster_classify_and_sequence_all_retries_fail(provider, mock_process_request):
    """Tests that an exception is raised after all retries are exhausted."""
    # Arrange
    from openai import APIConnectionError
    provider.max_retries = 2
    provider.client.chat.completions.parse.side_effect = APIConnectionError(request=MagicMock())

    # Act & Assert
    with pytest.raises(Exception, match="API Error after all retries"):
        with patch("app.services.openai_provider.time.sleep"): # mock sleep to speed up test
            provider.cluster_classify_and_sequence(
                request=mock_process_request, image_parts=[], prompt="test prompt", job_id="job6"
            )
    assert provider.client.chat.completions.parse.call_count == 2