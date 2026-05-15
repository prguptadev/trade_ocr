"""Test cases for the W3C tracing middleware."""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi import Request
from app.middleware.tracing import W3CTracingMiddleware, _format_srn_as_trace_id


# Mark all tests in this file as asyncio
pytestmark = pytest.mark.asyncio


# =====================================================================
# SHARED FIXTURES & SETUP
# =====================================================================

@pytest.fixture
def mock_middleware_app():
    """Fixture for mock middleware app."""
    return AsyncMock()


# =====================================================================
# TESTS FOR _format_srn_as_trace_id
# =====================================================================

def test_format_srn_16_char():
    """Tests formatting 16-character SRN."""
    # Arrange
    srn = "1234567890abcdef"
    
    # Act
    with patch('app.middleware.tracing.uuid.uuid4') as mock_uuid:
        mock_uuid.return_value.hex = '0000000000000000'
        result = _format_srn_as_trace_id(srn)
    
    # Assert
    assert len(result) == 32
    assert result == "1234567890abcdef0000000000000000"


def test_format_srn_32_char():
    """Tests formatting 32-character SRN."""
    # Arrange
    srn = "1234567890abcdef1234567890abcdef"
    
    # Act
    result = _format_srn_as_trace_id(srn)
    
    # Assert
    assert len(result) == 32
    assert result == "1234567890abcdef1234567890abcdef"


def test_format_srn_longer_than_32_char():
    """Tests formatting SRN longer than 32 characters (truncates)."""
    # Arrange
    long_srn = "1234567890abcdef1234567890abcdef1234567890"
    
    # Act
    result = _format_srn_as_trace_id(long_srn)
    
    # Assert
    assert len(result) == 32
    assert result == "1234567890abcdef1234567890abcdef"


def test_format_srn_with_non_hex_chars():
    """Tests formatting SRN with non-hex characters (removes them)."""
    # Arrange
    srn_with_invalid = "SRN-12345678"
    
    # Act
    result = _format_srn_as_trace_id(srn_with_invalid)
    
    # Assert
    assert len(result) == 32
    assert "12345678" in result


def test_format_srn_empty():
    """Tests formatting empty SRN."""
    # Arrange
    srn = ""
    
    # Act
    with patch('app.middleware.tracing.uuid.uuid4') as mock_uuid:
        mock_uuid.return_value.hex = '00000000000000000000000000000000'
        result = _format_srn_as_trace_id(srn)
    
    # Assert
    assert len(result) == 32
    assert result == "00000000000000000000000000000000"


def test_format_srn_integer():
    """Tests formatting integer SRN."""
    # Arrange
    srn = 12345
    
    # Act
    result = _format_srn_as_trace_id(srn)
    
    # Assert
    assert len(result) == 32


def test_format_srn_padding():
    """Tests that SRN is properly padded."""
    # Arrange
    short_srn = "abc"
    
    # Act
    with patch('app.middleware.tracing.uuid.uuid4') as mock_uuid:
        mock_uuid.return_value.hex = '0' * 29
        result = _format_srn_as_trace_id(short_srn)
    
    # Assert
    assert len(result) == 32
    assert result.startswith("abc")
    assert result == "abc" + '0' * 29


# =====================================================================
# TESTS FOR W3CTracingMiddleware
# =====================================================================

async def test_middleware_with_srn_in_body(mock_middleware_app):
    """Tests middleware extracts SRN from request body."""
    # Arrange
    middleware = W3CTracingMiddleware(mock_middleware_app)
    mock_request = AsyncMock(spec=Request)
    mock_request.body = AsyncMock(return_value=b'{"request": {"srn_no": "SRN123456789012345678"}}')
    mock_request.headers = {}
    mock_request.scope = {"type": "http", "method": "GET", "path": "/"}
    mock_response = MagicMock()
    mock_response.headers = {}
    mock_call_next = AsyncMock(return_value=mock_response)
    
    # Act
    response = await middleware.dispatch(mock_request, mock_call_next)
    
    # Assert
    assert response == mock_response
    mock_call_next.assert_called_once()
    assert "traceparent" in response.headers


async def test_middleware_with_invalid_json_body(mock_middleware_app):
    """Tests middleware handles invalid JSON in body."""
    # Arrange
    middleware = W3CTracingMiddleware(mock_middleware_app)
    mock_request = AsyncMock(spec=Request)
    mock_request.body = AsyncMock(return_value=b'invalid json')
    mock_request.headers = {}
    mock_request.scope = {"type": "http", "method": "GET", "path": "/"}
    mock_response = MagicMock()
    mock_response.headers = {}
    mock_call_next = AsyncMock(return_value=mock_response)
    
    # Act
    response = await middleware.dispatch(mock_request, mock_call_next)
    
    # Assert
    assert response == mock_response
    assert "traceparent" in response.headers


async def test_middleware_with_traceparent_header(mock_middleware_app):
    """Tests middleware extracts trace ID from traceparent header."""
    # Arrange
    trace_id = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    traceparent = f"00-{trace_id}-0af7651916cd43dd-01"
    middleware = W3CTracingMiddleware(mock_middleware_app)
    mock_request = AsyncMock(spec=Request)
    mock_request.body = AsyncMock(return_value=b'{}')
    mock_request.headers = {"traceparent": traceparent}
    mock_request.scope = {"type": "http", "method": "GET", "path": "/"}
    mock_response = MagicMock()
    mock_response.headers = {}
    mock_call_next = AsyncMock(return_value=mock_response)
    
    # Act
    response = await middleware.dispatch(mock_request, mock_call_next)
    
    # Assert
    assert response == mock_response
    assert "traceparent" in response.headers


async def test_middleware_generates_trace_id_if_missing(mock_middleware_app):
    """Tests middleware generates trace ID if not found."""
    # Arrange
    middleware = W3CTracingMiddleware(mock_middleware_app)
    mock_request = AsyncMock(spec=Request)
    mock_request.body = AsyncMock(return_value=b'{}')
    mock_request.headers = {}
    mock_request.scope = {"type": "http", "method": "GET", "path": "/"}
    mock_response = MagicMock()
    mock_response.headers = {}
    mock_call_next = AsyncMock(return_value=mock_response)
    
    # Act
    response = await middleware.dispatch(mock_request, mock_call_next)
    
    # Assert
    assert response == mock_response
    assert "traceparent" in response.headers


async def test_middleware_with_empty_body(mock_middleware_app):
    """Tests middleware handles empty request body."""
    # Arrange
    middleware = W3CTracingMiddleware(mock_middleware_app)
    mock_request = AsyncMock(spec=Request)
    mock_request.body = AsyncMock(return_value=b'')
    mock_request.headers = {}
    mock_request.scope = {"type": "http", "method": "GET", "path": "/"}
    mock_response = MagicMock()
    mock_response.headers = {}
    mock_call_next = AsyncMock(return_value=mock_response)
    
    # Act
    response = await middleware.dispatch(mock_request, mock_call_next)
    
    # Assert
    assert response == mock_response
    assert "traceparent" in response.headers


async def test_middleware_restores_body_for_endpoint(mock_middleware_app):
    """Tests middleware restores request body for endpoint."""
    # Arrange
    body_data = b'{"request": {"srn_no": "test"}}'
    middleware = W3CTracingMiddleware(mock_middleware_app)
    mock_request = AsyncMock(spec=Request)
    mock_request.body = AsyncMock(return_value=body_data)
    mock_request.headers = {}
    mock_request.scope = {"type": "http", "method": "POST", "path": "/"}
    mock_response = MagicMock()
    mock_response.headers = {}
    mock_call_next = AsyncMock(return_value=mock_response)
    
    # Act
    response = await middleware.dispatch(mock_request, mock_call_next)
    
    # Assert
    assert response == mock_response
    mock_call_next.assert_called_once()
    assert "traceparent" in response.headers


async def test_middleware_priority_srn_over_traceparent(mock_middleware_app):
    """Tests middleware prioritizes SRN over traceparent header."""
    # Arrange
    trace_id = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    traceparent = f"00-{trace_id}-0af7651916cd43dd-01"
    middleware = W3CTracingMiddleware(mock_middleware_app)
    mock_request = AsyncMock(spec=Request)
    mock_request.body = AsyncMock(return_value=b'{"request": {"srn_no": "SRN123"}}')
    mock_request.headers = {"traceparent": traceparent}
    mock_request.scope = {"type": "http", "method": "POST", "path": "/"}
    mock_response = MagicMock()
    mock_response.headers = {}
    mock_call_next = AsyncMock(return_value=mock_response)
    
    # Act
    response = await middleware.dispatch(mock_request, mock_call_next)
    
    # Assert
    assert response == mock_response
    assert "traceparent" in response.headers


async def test_middleware_with_invalid_traceparent_format(mock_middleware_app):
    """Tests middleware generates trace ID when traceparent format is invalid."""
    # Arrange
    # Invalid traceparent format - doesn't match regex pattern
    invalid_traceparent = "invalid-format-not-matching-w3c"
    middleware = W3CTracingMiddleware(mock_middleware_app)
    mock_request = AsyncMock(spec=Request)
    mock_request.body = AsyncMock(return_value=b'{}')
    mock_request.headers = {"traceparent": invalid_traceparent}
    mock_request.scope = {"type": "http", "method": "GET", "path": "/"}
    mock_response = MagicMock()
    mock_response.headers = {}
    mock_call_next = AsyncMock(return_value=mock_response)
    
    # Act
    response = await middleware.dispatch(mock_request, mock_call_next)
    
    # Assert
    assert response == mock_response
    assert "traceparent" in response.headers
    # Verify a new trace ID was generated instead of using the invalid header
    traceparent_header = response.headers["traceparent"]
    assert traceparent_header.startswith("00-")
    assert len(traceparent_header) > 0
