import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from app.services.llm_client import LLMOpenAIClient
from pydantic import BaseModel, Field

# Mark all tests in this file as asyncio
pytestmark = pytest.mark.asyncio


class MockResponseSchema(BaseModel):
    """A simple Pydantic model for testing response parsing."""
    message: str = Field(...)


@pytest.fixture
def mock_openai_client():
    """Fixture for a mocked OpenAI client with async methods."""
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.parse = AsyncMock()
    client.settings = MagicMock()
    client.settings.MODEL_NAME = "test-model"
    return client


@pytest.fixture
def llm_client(mock_openai_client):
    """Fixture to create an LLMOpenAIClient instance."""
    return LLMOpenAIClient(openai_client=mock_openai_client, max_retries=3, backoff_factor=0.1)


async def test_call_openai_with_retries_success(llm_client, mock_openai_client):
    """Tests a successful API call on the first attempt."""
    # Arrange
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(parsed=MockResponseSchema(message="success")))]
    mock_openai_client.chat.completions.parse.return_value = mock_response

    # Act
    result = await llm_client.call_openai_with_retries(
        prompt="test prompt",
        image_parts=[],
        response_schema=MockResponseSchema,
        context="test_success"
    )

    # Assert
    mock_openai_client.chat.completions.parse.assert_awaited_once()
    assert isinstance(result, MockResponseSchema)
    assert result.message == "success"


async def test_call_openai_with_retries_api_error_recovery(llm_client, mock_openai_client):
    """Tests recovery after a transient API error."""
    # Arrange
    from openai import RateLimitError
    mock_success_response = MagicMock()
    mock_success_response.choices = [MagicMock(message=MagicMock(parsed=MockResponseSchema(message="recovered")))]

    mock_openai_client.chat.completions.parse.side_effect = [
        RateLimitError("Rate limit", response=MagicMock(), body=None),
        mock_success_response
    ]

    # Act
    result = await llm_client.call_openai_with_retries(
        prompt="test prompt",
        image_parts=[],
        response_schema=MockResponseSchema,
        context="test_recovery"
    )

    # Assert
    assert mock_openai_client.chat.completions.parse.await_count == 2
    assert result.message == "recovered"


async def test_call_openai_with_retries_all_retries_fail(llm_client, mock_openai_client):
    """Tests the case where all retries for an API error fail."""
    # Arrange
    from openai import APIConnectionError
    llm_client.max_retries = 2
    mock_openai_client.chat.completions.parse.side_effect = APIConnectionError(request=MagicMock())

    # Act
    result = await llm_client.call_openai_with_retries(
        prompt="test prompt",
        image_parts=[],
        response_schema=MockResponseSchema,
        context="test_all_fail"
    )

    # Assert
    assert mock_openai_client.chat.completions.parse.await_count == 2
    assert result["error"] == "API Error after all retries"

async def test_call_openai_with_retries_validation_error_reask_success(llm_client, mock_openai_client):
    """Tests the targeted re-ask mechanism on a validation error."""
    # Arrange
    from pydantic import ValidationError
    
    # First call fails with validation error
    validation_error = ValidationError.from_exception_data(
        title="MockResponseSchema",
        line_errors=[{'loc': ('message',), 'msg': 'Field required', 'type': 'missing'}]
    )
    
    # The re-ask call succeeds
    mock_reask_response = MagicMock()
    mock_reask_response.choices = [MagicMock(message=MagicMock(parsed=MockResponseSchema(message="re-ask success")))]
    
    mock_openai_client.chat.completions.parse.side_effect = [
        validation_error,
        mock_reask_response
    ]

    # Act
    result = await llm_client.call_openai_with_retries(
        prompt="test prompt",
        image_parts=[],
        response_schema=MockResponseSchema,
        context="test_reask"
    )

    # Assert
    assert mock_openai_client.chat.completions.parse.await_count == 2
    assert isinstance(result, MockResponseSchema)
    assert result.message == "re-ask success"

async def test_call_openai_with_retries_validation_error_reask_fails(llm_client, mock_openai_client):
    """Tests when the initial call has a validation error and the re-ask also fails."""
    # Arrange
    from pydantic import ValidationError
    llm_client.json_correction_attempts = 2

    # First call fails with validation error
    validation_error = ValidationError.from_exception_data(
        title="MockResponseSchema",
        line_errors=[{'loc': ('message',), 'msg': 'Field required', 'type': 'missing'}]
    )
    
    # All re-ask attempts also fail (e.g., with another validation error)
    mock_openai_client.chat.completions.parse.side_effect = [
        validation_error,
        validation_error,
        validation_error
    ]

    # Act
    result = await llm_client.call_openai_with_retries(
        prompt="test prompt",
        image_parts=[],
        response_schema=MockResponseSchema,
        context="test_reask_fails"
    )

    # Assert
    # 1 initial call + 2 re-ask attempts
    assert mock_openai_client.chat.completions.parse.await_count == 3
    assert result["error"] == "Targeted re-ask failed after all attempts."


async def test_call_openai_with_retries_unexpected_error(llm_client, mock_openai_client):
    """Tests handling of an unexpected (non-API, non-Validation) error."""
    # Arrange
    mock_openai_client.chat.completions.parse.side_effect = ValueError("Something else went wrong")

    # Act
    result = await llm_client.call_openai_with_retries(
        prompt="test prompt", image_parts=[], response_schema=MockResponseSchema, context="test_unexpected"
    )

    # Assert
    assert result["error"] == "Unexpected error: Something else went wrong"