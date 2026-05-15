"""Test cases for the processing_service module."""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, Mock, call
from typing import Dict, Any, List

from app.services.processing_service import (
    _sanitize_base,
    _field_keys,
    _collect_group_field_names,
    _filter_prev_json_for_group,
    _get_page_prompt_template,
    _get_document_prompt_template,
    _extract_data_for_field_group,
    _extract_page_centric,
    _extract_document_centric,
    _extract_data_for_document,
    process_documents,
)
from app.api.v1.schemas import DocumentInput, DocumentResult, ProcessingRequest
from pydantic import BaseModel, Field


# Mark all tests in this file as asyncio
pytestmark = pytest.mark.asyncio


# =====================================================================
# SHARED FIXTURES & SETUP
# =====================================================================

@pytest.fixture
def mock_settings():
    """Fixture for mocked settings."""
    settings = MagicMock()
    settings.API_BASE_URL = "https://api.test.com"
    settings.API_KEY = "test-key-123"
    settings.API_MODEL = "test-model"
    settings.API_TIMEOUT = 30
    settings.API_CONCURRENCY_LIMIT = 5
    settings.API_MAX_RETRIES = 3
    settings.JSON_CORRECTION_ATTEMPTS = 2
    settings.DOCUMENT_FIELDS = {
        "CRL": {
            "page_wise": {},
            "document_wise": {}
        },
        "INVOICE": {
            "page_wise": {},
            "document_wise": {}
        }
    }
    return settings


@pytest.fixture
def mock_logger():
    """Fixture for mocked logger."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.debug = MagicMock()
    return logger


@pytest.fixture
def sample_pydantic_model():
    """Fixture for a sample Pydantic model."""
    class SampleModel(BaseModel):
        name: str = Field(...)
        value: int = Field(...)
        description: str = Field(default="")
    
    return SampleModel


# =====================================================================
# FIXTURES
# =====================================================================

@pytest.fixture
def sample_field():
    """Fixture for a sample field definition."""
    return {
        "name": "applicant_name",
        "description": "The applicant's full name",
        "validation_regex": "^[a-zA-Z\\s]+$"
    }


@pytest.fixture
def sample_fields():
    """Fixture for multiple sample field definitions."""
    return [
        {
            "name": "applicant_name",
            "description": "The applicant's full name",
            "validation_regex": "^[a-zA-Z\\s]+$"
        },
        {
            "name": "applicant_address",
            "description": "The applicant's address",
            "validation_regex": None
        },
        {
            "name": "invoice_no",
            "description": "Invoice number",
            "validation_regex": "^[0-9]{6}$"
        }
    ]


@pytest.fixture
def sample_extracted_json():
    """Fixture for sample extracted JSON data."""
    return {
        "applicant_name_Value": "John Doe",
        "applicant_name_Confidence": "HIGH",
        "applicant_address_Value": "123 Main St",
        "applicant_address_Confidence": "MEDIUM",
        "invoice_no_Value": "123456",
        "invoice_no_Confidence": "HIGH",
        "unknown_field_Value": "some_value",
        "unknown_field_Confidence": "LOW"
    }


@pytest.fixture
def sample_document_input():
    """Fixture for sample document input."""
    return DocumentInput(
        document_id="DOC001",
        document_type="CRL",
        pages=["page1.png", "page2.png"],
        confidence_score=0.95
    )


# =====================================================================
# TESTS FOR _sanitize_base
# =====================================================================

def test_sanitize_base_alphanumeric():
    """Tests sanitization of alphanumeric string."""
    # Arrange
    name = "applicant_name"

    # Act
    result = _sanitize_base(name)

    # Assert
    assert result == "applicant_name"


def test_sanitize_base_with_special_chars():
    """Tests sanitization of string with special characters."""
    # Arrange
    name = "field-with-special_chars!"

    # Act
    result = _sanitize_base(name)

    # Assert
    assert result == "field_with_special_chars_"


def test_sanitize_base_all_special_chars():
    """Tests sanitization of string with only special characters."""
    # Arrange
    name = "!@#$%^&*()"

    # Act
    result = _sanitize_base(name)

    # Assert
    assert result == "__________"


def test_sanitize_base_with_numbers():
    """Tests sanitization preserves numbers."""
    # Arrange
    name = "field123_name456"

    # Act
    result = _sanitize_base(name)

    # Assert
    assert result == "field123_name456"


# =====================================================================
# TESTS FOR _field_keys
# =====================================================================

def test_field_keys_generation():
    """Tests generation of field keys."""
    # Arrange
    field_name = "applicant_name"

    # Act
    result = _field_keys(field_name)

    # Assert
    assert "applicant_name_Value" in result
    assert "applicant_name_Confidence" in result
    assert len(result) == 2


def test_field_keys_with_special_chars():
    """Tests field key generation with special characters."""
    # Arrange
    field_name = "field-with-special!"

    # Act
    result = _field_keys(field_name)

    # Assert
    assert "field_with_special__Value" in result
    assert "field_with_special__Confidence" in result


def test_field_keys_returns_set():
    """Tests that _field_keys returns a set."""
    # Arrange
    field_name = "test_field"

    # Act
    result = _field_keys(field_name)

    # Assert
    assert isinstance(result, set)


# =====================================================================
# TESTS FOR _collect_group_field_names
# =====================================================================

def test_collect_group_field_names_single_field(sample_field):
    """Tests collection of single field name."""
    # Arrange
    fields = [sample_field]

    # Act
    result = _collect_group_field_names(fields)

    # Assert
    assert "applicant_name" in result
    assert len(result) == 1


def test_collect_group_field_names_multiple_fields(sample_fields):
    """Tests collection of multiple field names."""
    # Arrange
    # Act
    result = _collect_group_field_names(sample_fields)

    # Assert
    assert "applicant_name" in result
    assert "applicant_address" in result
    assert "invoice_no" in result
    assert len(result) == 3


def test_collect_group_field_names_empty_list():
    """Tests collection from empty field list."""
    # Arrange
    fields = []

    # Act
    result = _collect_group_field_names(fields)

    # Assert
    assert len(result) == 0
    assert isinstance(result, set)


def test_collect_group_field_names_invalid_entries(sample_field):
    """Tests collection with invalid field entries."""
    # Arrange
    fields = [sample_field, {"invalid": "entry"}, {"name": "valid_name"}]

    # Act
    result = _collect_group_field_names(fields)

    # Assert
    assert "applicant_name" in result
    assert "valid_name" in result
    assert len(result) == 2


# =====================================================================
# TESTS FOR _filter_prev_json_for_group
# =====================================================================

def test_filter_prev_json_for_group_basic(sample_extracted_json, sample_fields):
    """Tests filtering of previous JSON for group fields."""
    # Arrange
    # Act
    result = _filter_prev_json_for_group(
        extracted_json=sample_extracted_json,
        unique_fields=sample_fields,
        keep_non_null_only=True
    )

    # Assert
    assert "applicant_name_Value" in result
    assert "applicant_name_Confidence" in result
    assert "invoice_no_Value" in result
    assert "unknown_field_Value" not in result


def test_filter_prev_json_for_group_with_always_include(sample_extracted_json):
    """Tests filtering with always-include field names."""
    # Arrange
    fields = [{"name": "unknown_field"}]
    always_include = ["applicant_name"]

    # Act
    result = _filter_prev_json_for_group(
        extracted_json=sample_extracted_json,
        unique_fields=fields,
        always_include_field_names=always_include,
        keep_non_null_only=True
    )

    # Assert
    assert "applicant_name_Value" in result
    assert "applicant_name_Confidence" in result


def test_filter_prev_json_for_group_keep_null_values(sample_fields):
    """Tests filtering while keeping null values."""
    # Arrange
    extracted_json = {
        "applicant_name_Value": "John",
        "applicant_name_Confidence": "HIGH",
        "applicant_address_Value": None,
        "applicant_address_Confidence": "NOT_FOUND"
    }

    # Act
    result = _filter_prev_json_for_group(
        extracted_json=extracted_json,
        unique_fields=sample_fields,
        keep_non_null_only=False
    )

    # Assert
    assert "applicant_address_Value" in result
    assert result["applicant_address_Value"] is None


def test_filter_prev_json_for_group_empty_values():
    """Tests filtering with various empty values."""
    # Arrange
    extracted_json = {
        "field1_Value": "",
        "field1_Confidence": "HIGH",
        "field2_Value": [],
        "field2_Confidence": "LOW",
        "field3_Value": {},
        "field3_Confidence": "MEDIUM"
    }
    fields = [{"name": "field1"}, {"name": "field2"}, {"name": "field3"}]

    # Act
    result = _filter_prev_json_for_group(
        extracted_json=extracted_json,
        unique_fields=fields,
        keep_non_null_only=True
    )

    # Assert
    # Only confidence values are kept since their values are not empty
    assert "field1_Confidence" in result
    assert "field2_Confidence" in result
    assert "field3_Confidence" in result


# =====================================================================
# TESTS FOR _get_page_prompt_template
# =====================================================================

def test_get_page_prompt_template_crl():
    """Tests retrieving page prompt template for CRL."""
    # Arrange & Act
    with patch('app.services.processing_service.EXTRACTION_PROMPT_TEMPLATE_PAGE_CRL', 'CRL_TEMPLATE'):
        result = _get_page_prompt_template("CRL")

    # Assert
    assert result == 'CRL_TEMPLATE'


def test_get_page_prompt_template_invoice():
    """Tests retrieving page prompt template for INVOICE."""
    # Arrange & Act
    with patch('app.services.processing_service.EXTRACTION_PROMPT_TEMPLATE_PAGE_INVOICE', 'INVOICE_TEMPLATE'):
        result = _get_page_prompt_template("INVOICE")

    # Assert
    assert result == 'INVOICE_TEMPLATE'


def test_get_page_prompt_template_unknown_type():
    """Tests retrieving page prompt template for unknown document type."""
    # Arrange & Act
    with patch('app.services.processing_service.EXTRACTION_PROMPT_TEMPLATE_PAGE_INVOICE', 'INVOICE_DEFAULT'):
        result = _get_page_prompt_template("UNKNOWN")

    # Assert
    assert result == 'INVOICE_DEFAULT'


# =====================================================================
# TESTS FOR _get_document_prompt_template
# =====================================================================

def test_get_document_prompt_template_crl():
    """Tests retrieving document prompt template for CRL."""
    # Arrange & Act
    with patch('app.services.processing_service.EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_CRL', 'CRL_DOC_TEMPLATE'):
        result = _get_document_prompt_template("CRL")

    # Assert
    assert result == 'CRL_DOC_TEMPLATE'


def test_get_document_prompt_template_invoice():
    """Tests retrieving document prompt template for INVOICE."""
    # Arrange & Act
    with patch('app.services.processing_service.EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_INVOICE', 'INVOICE_DOC_TEMPLATE'):
        result = _get_document_prompt_template("INVOICE")

    # Assert
    assert result == 'INVOICE_DOC_TEMPLATE'


def test_get_document_prompt_template_unknown_type():
    """Tests retrieving document prompt template for unknown type."""
    # Arrange & Act
    with patch('app.services.processing_service.EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_INVOICE', 'INVOICE_DOC_DEFAULT'):
        result = _get_document_prompt_template("UNKNOWN")

    # Assert
    assert result == 'INVOICE_DOC_DEFAULT'


# =====================================================================
# TESTS FOR _extract_data_for_field_group
# =====================================================================

async def test_extract_data_for_field_group_success():
    """Tests successful field group extraction."""
    # Arrange
    from pydantic import BaseModel, Field, create_model

    ResponseSchema = create_model(
        'TestSchema',
        test_field=(str, Field(...)),
        test_field2=(str, Field(...))
    )

    mock_group_context = "test_context"
    mock_prompt = "Extract data"
    mock_files = ["page1.png"]
    mock_schema = ResponseSchema

    mock_response = {"test_field_Value": "value1", "test_field2_Value": "value2"}
    mock_metrics = {"latency_ms": 100}

    with patch('app.services.processing_service.llm_client.call_llm_with_parsing') as mock_llm:
        mock_llm.return_value = (mock_response, mock_metrics)

        # Act
        result, metrics = await _extract_data_for_field_group(
            group_context=mock_group_context,
            prompt=mock_prompt,
            full_page_paths=mock_files,
            extraction_schema=mock_schema,
            group_name="test_group",
            job_id="JOB001"
        )

    # Assert
    assert result is not None
    assert isinstance(metrics, dict)


async def test_extract_data_for_field_group_failure():
    """Tests field group extraction failure handling."""
    # Arrange
    from pydantic import BaseModel, Field, create_model

    ResponseSchema = create_model(
        'TestSchema',
        test_field=(str, Field(...))
    )

    mock_group_context = "test_context_fail"
    mock_prompt = "Extract data"
    mock_files = ["page1.png"]

    with patch('app.services.processing_service.llm_client.call_llm_with_parsing') as mock_llm:
        mock_llm.return_value = ({"error": "LLM failed"}, {"latency_ms": 50, "token_usage": {}})

        # Act
        result, metrics = await _extract_data_for_field_group(
            group_context=mock_group_context,
            prompt=mock_prompt,
            full_page_paths=mock_files,
            extraction_schema=ResponseSchema,
            group_name="test_group",
            job_id="JOB001"
        )

    # Assert - should return error dict when LLM fails
    assert isinstance(result, dict)
    assert "error" in result


# =====================================================================
# INTEGRATION TESTS
# =====================================================================

def test_sanitize_and_field_keys_integration():
    """Tests integration between _sanitize_base and _field_keys."""
    # Arrange
    field_name = "complex-field_name!@#"

    # Act
    sanitized = _sanitize_base(field_name)
    keys = _field_keys(field_name)

    # Assert
    assert "complex_field_name____Value" in keys
    assert "complex_field_name____Confidence" in keys
    assert len(keys) == 2


def test_filter_and_collect_integration(sample_extracted_json):
    """Tests integration between filtering and collection."""
    # Arrange
    fields = [
        {"name": "applicant_name"},
        {"name": "applicant_address"},
        {"name": "invoice_no"}
    ]

    # Act
    collected_names = _collect_group_field_names(fields)
    filtered_json = _filter_prev_json_for_group(
        extracted_json=sample_extracted_json,
        unique_fields=fields,
        keep_non_null_only=True
    )

    # Assert
    assert len(collected_names) == 3
    assert len(filtered_json) > 0


async def test_prompt_template_selection_integration():
    """Tests integration of prompt template selection."""
    # Arrange
    crl_page_template = "CRL_PAGE"
    crl_doc_template = "CRL_DOC"
    invoice_page_template = "INVOICE_PAGE"
    invoice_doc_template = "INVOICE_DOC"

    with patch('app.services.processing_service.EXTRACTION_PROMPT_TEMPLATE_PAGE_CRL', crl_page_template):
        with patch('app.services.processing_service.EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_CRL', crl_doc_template):
            with patch('app.services.processing_service.EXTRACTION_PROMPT_TEMPLATE_PAGE_INVOICE', invoice_page_template):
                with patch('app.services.processing_service.EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_INVOICE', invoice_doc_template):
                    # Act
                    crl_page = _get_page_prompt_template("CRL")
                    crl_doc = _get_document_prompt_template("CRL")
                    inv_page = _get_page_prompt_template("INVOICE")
                    inv_doc = _get_document_prompt_template("INVOICE")

    # Assert
    assert crl_page == crl_page_template
    assert crl_doc == crl_doc_template
    assert inv_page == invoice_page_template
    assert inv_doc == invoice_doc_template


# =====================================================================
# EDGE CASE TESTS
# =====================================================================

def test_sanitize_base_unicode_characters():
    """Tests sanitization with unicode characters."""
    # Arrange
    name = "field_名前_name"

    # Act
    result = _sanitize_base(name)

    # Assert
    assert isinstance(result, str)


def test_field_keys_empty_string():
    """Tests field key generation with empty string."""
    # Arrange
    field_name = ""

    # Act
    result = _field_keys(field_name)

    # Assert
    assert "_Value" in result
    assert "_Confidence" in result


def test_collect_group_field_names_with_duplicates(sample_field):
    """Tests collection handles duplicate field names."""
    # Arrange
    fields = [sample_field, sample_field, sample_field]

    # Act
    result = _collect_group_field_names(fields)

    # Assert
    assert "applicant_name" in result
    assert len(result) == 1  # Set should eliminate duplicates


def test_filter_prev_json_empty_extracted_json():
    """Tests filtering with empty extracted JSON."""
    # Arrange
    extracted_json = {}
    fields = [{"name": "field1"}, {"name": "field2"}]

    # Act
    result = _filter_prev_json_for_group(
        extracted_json=extracted_json,
        unique_fields=fields
    )

    # Assert
    assert result == {}


# =====================================================================
# ADDITIONAL COMPREHENSIVE TESTS FOR 100% COVERAGE
# =====================================================================

# Additional tests for edge cases and error paths
def test_sanitize_base_leading_special_chars():
    """Tests sanitization with leading special characters."""
    # Arrange
    name = "!!!field_name"

    # Act
    result = _sanitize_base(name)

    # Assert
    assert result == "___field_name"


def test_sanitize_base_trailing_special_chars():
    """Tests sanitization with trailing special characters."""
    # Arrange
    name = "field_name!!!"

    # Act
    result = _sanitize_base(name)

    # Assert
    assert result == "field_name___"


def test_sanitize_base_consecutive_special_chars():
    """Tests sanitization with consecutive special characters."""
    # Arrange
    name = "field@@##name"

    # Act
    result = _sanitize_base(name)

    # Assert
    assert result == "field____name"


def test_field_keys_single_char():
    """Tests field key generation with single character."""
    # Arrange
    field_name = "a"

    # Act
    result = _field_keys(field_name)

    # Assert
    assert result == {"a_Value", "a_Confidence"}


def test_collect_group_field_names_with_none_values():
    """Tests collecting group field names when dicts have None values."""
    # Arrange
    unique_fields = [
        {"name": "field1"},
        {"name": None},
        {"other_key": "value"},
        {"name": "field2"}
    ]

    # Act
    result = _collect_group_field_names(unique_fields)

    # Assert
    assert "field1" in result
    assert "field2" in result
    assert len(result) == 2


def test_collect_group_field_names_non_dict_entries():
    """Tests collecting group field names with non-dict entries."""
    # Arrange
    unique_fields = [
        {"name": "field1"},
        "invalid_string",
        None,
        {"name": "field2"}
    ]

    # Act
    result = _collect_group_field_names(unique_fields)

    # Assert
    assert "field1" in result
    assert "field2" in result


def test_filter_prev_json_mixed_null_values():
    """Tests filtering with various null-like values."""
    # Arrange
    extracted_json = {
        "field1_Value": None,
        "field1_Confidence": "HIGH",
        "field2_Value": 0,
        "field2_Confidence": 1,
        "field3_Value": False,
        "field3_Confidence": True
    }
    fields = [{"name": "field1"}, {"name": "field2"}, {"name": "field3"}]

    # Act
    result = _filter_prev_json_for_group(
        extracted_json=extracted_json,
        unique_fields=fields,
        keep_non_null_only=True
    )

    # Assert
    assert "field1_Value" not in result  # None is filtered
    assert "field1_Confidence" in result
    assert "field2_Value" in result  # 0 is kept
    assert "field2_Confidence" in result
    assert "field3_Value" in result  # False is kept
    assert "field3_Confidence" in result


def test_filter_prev_json_keep_null_false():
    """Tests filtering with keep_non_null_only=False."""
    # Arrange
    extracted_json = {
        "field1_Value": None,
        "field1_Confidence": "HIGH",
        "field2_Value": "",
        "field2_Confidence": "LOW"
    }
    fields = [{"name": "field1"}, {"name": "field2"}]

    # Act
    result = _filter_prev_json_for_group(
        extracted_json=extracted_json,
        unique_fields=fields,
        keep_non_null_only=False
    )

    # Assert
    assert "field1_Value" in result
    assert "field1_Confidence" in result
    assert "field2_Value" in result
    assert "field2_Confidence" in result
    assert result["field1_Value"] is None


def test_filter_prev_json_always_include_overrides_filtering():
    """Tests that always_include fields are included even without unique_fields."""
    # Arrange
    extracted_json = {
        "applicant_name_Value": "John Doe",
        "applicant_name_Confidence": "HIGH",
        "field1_Value": "data",
        "field1_Confidence": "MEDIUM"
    }
    fields = []  # Empty unique fields
    always_include = ["applicant_name"]

    # Act
    result = _filter_prev_json_for_group(
        extracted_json=extracted_json,
        unique_fields=fields,
        always_include_field_names=always_include,
        keep_non_null_only=True
    )

    # Assert
    assert "applicant_name_Value" in result
    assert "applicant_name_Confidence" in result
    assert "field1_Value" not in result


def test_get_page_prompt_template_crl():
    """Tests getting page prompt template for CRL documents."""
    # Act
    result = _get_page_prompt_template("CRL")

    # Assert
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_page_prompt_template_invoice():
    """Tests getting page prompt template for INVOICE documents."""
    # Act
    result = _get_page_prompt_template("INVOICE")

    # Assert
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_page_prompt_template_default_unknown():
    """Tests that unknown doc type defaults to INVOICE."""
    # Act
    result = _get_page_prompt_template("UNKNOWN")

    # Assert
    invoice_template = _get_page_prompt_template("INVOICE")
    assert result == invoice_template


def test_get_document_prompt_template_crl():
    """Tests getting document prompt template for CRL documents."""
    # Act
    result = _get_document_prompt_template("CRL")

    # Assert
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_document_prompt_template_invoice():
    """Tests getting document prompt template for INVOICE documents."""
    # Act
    result = _get_document_prompt_template("INVOICE")

    # Assert
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_document_prompt_template_default_unknown():
    """Tests that unknown doc type defaults to INVOICE for document level."""
    # Act
    result = _get_document_prompt_template("UNKNOWN")

    # Assert
    invoice_template = _get_document_prompt_template("INVOICE")
    assert result == invoice_template


# Additional async function tests with mocking
@pytest.mark.asyncio
async def test_extract_data_for_field_group_with_base_model_response(mock_settings, mock_logger):
    """Tests extraction when LLM returns a BaseModel response."""
    # Arrange
    class TestSchema(BaseModel):
        field: str = Field(default="value")
    
    mock_response = TestSchema(field="test_value")
    
    with patch('app.services.processing_service.llm_client') as mock_llm:
        mock_llm.call_llm_with_parsing = AsyncMock(
            return_value=(mock_response, {"test": "metrics"})
        )
        
        # Act
        result, metrics = await _extract_data_for_field_group(
            group_context="test_context",
            prompt="test_prompt",
            full_page_paths=["/path/to/page"],
            extraction_schema=TestSchema,
            group_name="test_group",
            job_id="job123"
        )
        
        # Assert
        assert result == {"field": "test_value"}
        assert metrics == {"test": "metrics"}


@pytest.mark.asyncio
async def test_extract_data_for_field_group_with_dict_error(mock_settings, mock_logger):
    """Tests extraction when LLM returns a dict with error."""
    # Arrange
    error_response = {
        "error": "Validation failed",
        "raw_response": "Invalid JSON structure"
    }
    
    with patch('app.services.processing_service.llm_client') as mock_llm:
        mock_llm.call_llm_with_parsing = AsyncMock(
            return_value=(error_response, {})
        )
        
        mock_schema = type('TestSchema', (BaseModel,), {})
        
        # Act
        result, metrics = await _extract_data_for_field_group(
            group_context="test_context",
            prompt="test_prompt",
            full_page_paths=["/path/to/page"],
            extraction_schema=mock_schema,
            group_name="test_group"
        )
        
        # Assert - should return exact error response from LLM
        assert result == error_response
        assert "error" in result
        assert result["error"] == "Validation failed"


@pytest.mark.asyncio
async def test_extract_data_for_field_group_non_dict_error(mock_settings, mock_logger):
    """Tests extraction when LLM returns other error type."""
    # Arrange
    with patch('app.services.processing_service.llm_client') as mock_llm:
        mock_llm.call_llm_with_parsing = AsyncMock(
            return_value=(None, {})
        )
        
        mock_schema = type('TestSchema', (BaseModel,), {})
        
        # Act
        result, metrics = await _extract_data_for_field_group(
            group_context="test_context",
            prompt="test_prompt",
            full_page_paths=["/path/to/page"],
            extraction_schema=mock_schema,
            group_name="test_group"
        )
        
        # Assert
        assert result is None


def test_filter_prev_json_with_numeric_values():
    """Tests filtering preserves numeric values correctly."""
    # Arrange
    extracted_json = {
        "field1_Value": 123,
        "field1_Confidence": 0.95,
        "field2_Value": -50,
        "field2_Confidence": 1.0
    }
    fields = [{"name": "field1"}, {"name": "field2"}]

    # Act
    result = _filter_prev_json_for_group(
        extracted_json=extracted_json,
        unique_fields=fields,
        keep_non_null_only=True
    )

    # Assert
    assert result["field1_Value"] == 123
    assert result["field1_Confidence"] == 0.95
    assert result["field2_Value"] == -50
    assert result["field2_Confidence"] == 1.0


def test_field_keys_numeric_field_name():
    """Tests field key generation with numeric characters in name."""
    # Arrange
    field_name = "field123"

    # Act
    result = _field_keys(field_name)

    # Assert
    assert "field123_Value" in result
    assert "field123_Confidence" in result


def test_collect_group_field_names_empty_name():
    """Tests collecting group field names with empty string names."""
    # Arrange
    unique_fields = [
        {"name": ""},
        {"name": "field1"},
        {}
    ]

    # Act
    result = _collect_group_field_names(unique_fields)

    # Assert
    assert "" not in result
    assert "field1" in result


def test_filter_prev_json_complex_nested_values():
    """Tests filtering with complex nested data structures."""
    # Arrange
    extracted_json = {
        "field1_Value": {"nested": "data"},
        "field1_Confidence": "HIGH",
        "field2_Value": ["item1", "item2"],
        "field2_Confidence": "MEDIUM"
    }
    fields = [{"name": "field1"}, {"name": "field2"}]

    # Act
    result = _filter_prev_json_for_group(
        extracted_json=extracted_json,
        unique_fields=fields,
        keep_non_null_only=False
    )

    # Assert
    assert result["field1_Value"] == {"nested": "data"}
    assert result["field2_Value"] == ["item1", "item2"]


def test_sanitize_base_only_underscores():
    """Tests sanitization when input is all underscores."""
    # Arrange
    name = "______"

    # Act
    result = _sanitize_base(name)

    # Assert
    assert result == "______"


def test_sanitize_base_mixed_case():
    """Tests that sanitization preserves case."""
    # Arrange
    name = "FieldName_123"

    # Act
    result = _sanitize_base(name)

    # Assert
    assert result == "FieldName_123"


def test_filter_prev_json_single_field():
    """Tests filtering with a single field."""
    # Arrange
    extracted_json = {
        "field1_Value": "test",
        "field1_Confidence": "HIGH",
        "other_Value": "ignored",
        "other_Confidence": "LOW"
    }
    fields = [{"name": "field1"}]

    # Act
    result = _filter_prev_json_for_group(
        extracted_json=extracted_json,
        unique_fields=fields
    )

    # Assert
    assert "field1_Value" in result
    assert "field1_Confidence" in result
    assert "other_Value" not in result
    assert "other_Confidence" not in result


def test_collect_group_field_names_preserves_order():
    """Tests that set is created correctly regardless of order."""
    # Arrange
    unique_fields_1 = [
        {"name": "field_a"},
        {"name": "field_b"},
        {"name": "field_c"}
    ]
    unique_fields_2 = [
        {"name": "field_c"},
        {"name": "field_a"},
        {"name": "field_b"}
    ]

    # Act
    result_1 = _collect_group_field_names(unique_fields_1)
    result_2 = _collect_group_field_names(unique_fields_2)

    # Assert
    assert result_1 == result_2


def test_sanitize_base_hyphenated_words():
    """Tests sanitization with hyphenated words to underscores."""
    # Arrange
    name = "field-with-hyphens"

    # Act
    result = _sanitize_base(name)

    # Assert
    assert result == "field_with_hyphens"


def test_field_keys_unicode_characters():
    """Tests field key generation with unicode characters."""
    # Arrange
    field_name = "café"

    # Act
    result = _field_keys(field_name)

    # Assert
    # Unicode chars should become underscores
    assert len(result) == 2
    assert any("Value" in key for key in result)
    assert any("Confidence" in key for key in result)


# =====================================================================
# ASYNC INTEGRATION TESTS FOR LARGE FUNCTIONS
# =====================================================================


@pytest.mark.asyncio
async def test_extract_page_centric_basic_flow(mock_settings, mock_logger):
    """Tests basic page-centric extraction flow."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        mock_llm.call_llm_with_parsing = AsyncMock(
            return_value=({"field1_Value": "test"}, {})
        )
        
        doc_input = DocumentInput(
            document_id="DOC001",
            document_type="INVOICE",
            pages=["page1.png"]
        )
        
        field_groups = {
            "basic_info": [
                {"name": "buyer_name"},
                {"name": "seller_name"}
            ]
        }
        
        # Act
        result, metrics, failures = await _extract_page_centric(
            job_id="JOB001",
            doc_input=doc_input,
            base_path="/test/docs",
            field_groups=field_groups
        )
        
        # Assert
        assert isinstance(result, dict)
        assert isinstance(metrics, dict)
        assert isinstance(failures, list)


@pytest.mark.asyncio
async def test_extract_document_centric_flow(mock_settings, mock_logger):
    """Tests document-centric extraction flow."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        with patch('app.services.processing_service.create_extraction_model') as mock_create_model:
            mock_llm.call_llm_with_parsing = AsyncMock(
                return_value=({"total_amount_Value": "5000"}, {"latency_ms": 100, "token_usage": {}})
            )
            # create_extraction_model returns a Pydantic model class
            mock_create_model.return_value = Mock()
            
            doc_input = DocumentInput(
                document_id="DOC002",
                document_type="INVOICE",
                pages=["page1.png", "page2.png"]
            )
            
            field_groups = {
                "totals": [{"name": "total_amount", "description": "Total invoice amount"}]
            }
            
            # Act
            result, metrics, failures = await _extract_document_centric(
                job_id="JOB002",
                doc_input=doc_input,
                base_path="/test/docs",
                field_groups=field_groups
            )
            
            # Assert
            assert isinstance(result, dict)
            assert isinstance(metrics, dict)
            assert isinstance(failures, list)


@pytest.mark.asyncio
async def test_extract_data_for_document_flow(mock_settings, mock_logger):
    """Tests document-level extraction orchestration."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        with patch('app.services.processing_service.create_extraction_model') as mock_create_model:
            mock_llm.call_llm_with_parsing = AsyncMock(
                return_value=({"applicant_name_Value": "John Doe"}, {"latency_ms": 100, "token_usage": {}})
            )
            mock_create_model.return_value = Mock()
            
            doc_input = DocumentInput(
                document_id="DOC003",
                document_type="CRL",
                pages=["page1.png"]
            )
            
            # Act - _extract_data_for_document uses settings.DOCUMENT_FIELDS, not field_groups
            result, metrics = await _extract_data_for_document(
                job_id="JOB003",
                doc_input=doc_input,
                base_path="/test/docs"
            )
            
            # Assert
            assert result is not None
            assert isinstance(metrics, dict)


@pytest.mark.asyncio
async def test_process_documents_basic(mock_settings, mock_logger):
    """Tests the main document processing pipeline."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        with patch('app.services.processing_service.create_extraction_model') as mock_create_model:
            mock_llm.call_llm_with_parsing = AsyncMock(
                return_value=({"invoice_no_Value": "INV001"}, {"latency_ms": 100, "token_usage": {}})
            )
            mock_create_model.return_value = Mock()
            
            doc1 = DocumentInput(
                document_id="DOC001",
                document_type="INVOICE",
                pages=["page1.png"]
            )
            
            # Create request with all required fields
            from app.api.v1.schemas import ProcessFolderRequest, ProcessingMetadata, TokenUsage
            
            metadata = ProcessingMetadata(
                ai_call_latency_ms=100.0,
                token_usage=TokenUsage(
                    completion_tokens=10,
                    prompt_tokens=50,
                    total_tokens=60
                )
            )
            
            folder_request = ProcessFolderRequest(
                folder_path="/test/docs",
                srn_no="SRN001"
            )
            
            request = ProcessingRequest(
                request=folder_request,
                job_id="JOB001",
                documents=[doc1],
                processing_metadata=metadata
            )
            
            # Act
            result = await process_documents(
                request=request,
                file_path="/test/docs"
            )
            
            # Assert
            assert result is not None
            assert hasattr(result, 'status')


@pytest.mark.asyncio
async def test_doc_type_crl_processing(mock_settings, mock_logger):
    """Tests CRL document type processing."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        mock_llm.call_llm_with_parsing = AsyncMock(
            return_value=({}, {})
        )
        
        doc_input = DocumentInput(
            document_id="CRL001",
            document_type="CRL",
            pages=["crl_page.png"]
        )
        
        field_groups = {"personal": [{"name": "applicant_name"}]}
        
        # Act
        result, metrics, failures = await _extract_page_centric(
            job_id="JOB_CRL",
            doc_input=doc_input,
            base_path="/test/crls",
            field_groups=field_groups
        )
        
        # Assert
        assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_extraction_with_empty_page_list(mock_settings, mock_logger):
    """Tests handling of documents with no pages."""
    doc_input = DocumentInput(
        document_id="EMPTY001",
        document_type="INVOICE",
        pages=[]
    )
    
    field_groups = {"info": [{"name": "field1"}]}
    
    # Act
    result, metrics, failures = await _extract_page_centric(
        job_id="JOB_EMPTY",
        doc_input=doc_input,
        base_path="/test/docs",
        field_groups=field_groups
    )
    
    # Assert
    assert isinstance(result, dict)
    assert isinstance(failures, list)


@pytest.mark.asyncio
async def test_extraction_with_llm_error_handling(mock_settings, mock_logger):
    """Tests extraction when LLM fails."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        # Mock LLM to return error dict with metrics
        mock_llm.call_llm_with_parsing = AsyncMock(
            return_value=({"error": "LLM failed"}, {"latency_ms": 100, "token_usage": {"prompt_tokens": 20, "completion_tokens": 0, "total_tokens": 20, "prompt_tokens_details": {}}})
        )
        
        doc_input = DocumentInput(
            document_id="ERROR001",
            document_type="INVOICE",
            pages=["page1.png"]
        )
        
        field_groups = {"group1": [{"name": "field1"}]}
        
        # Act
        result, metrics, failures = await _extract_page_centric(
            job_id="JOB_ERROR",
            doc_input=doc_input,
            base_path="/test/docs",
            field_groups=field_groups
        )
        
        # Assert
        assert isinstance(result, dict)
        assert isinstance(failures, list)


@pytest.mark.asyncio
async def test_multi_group_extraction(mock_settings, mock_logger):
    """Tests extraction with multiple field groups."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        mock_llm.call_llm_with_parsing = AsyncMock(
            return_value=({"response": "value"}, {})
        )
        
        doc_input = DocumentInput(
            document_id="MULTI001",
            document_type="INVOICE",
            pages=["page1.png"]
        )
        
        field_groups = {
            "group1": [{"name": "field1"}],
            "group2": [{"name": "field2"}],
            "group3": [{"name": "field3"}]
        }
        
        # Act
        result, metrics, failures = await _extract_page_centric(
            job_id="JOB_MULTI",
            doc_input=doc_input,
            base_path="/test/docs",
            field_groups=field_groups
        )
        
        # Assert
        assert isinstance(result, dict)
        assert mock_llm.call_llm_with_parsing.call_count >= 1

# =====================================================================
# ADDITIONAL COVERAGE TESTS FOR EDGE CASES
# =====================================================================

@pytest.mark.asyncio
async def test_unsupported_document_type(mock_settings, mock_logger):
    """Tests handling of unsupported document type (lines 399-402)."""
    doc_input = DocumentInput(
        document_id="DOC_UNSUPPORTED",
        document_type="UNKNOWN_TYPE",
        pages=["page1.png"]
    )
    
    # Act
    result, metrics = await _extract_data_for_document(
        job_id="JOB_UNSUPPORTED",
        doc_input=doc_input,
        base_path="/test/docs"
    )
    
    # Assert
    assert result.document_id == "DOC_UNSUPPORTED"
    assert result.processing_status == "Skipped"


@pytest.mark.asyncio
async def test_malformed_field_groups(mock_settings, mock_logger):
    """Tests handling of malformed field groups (lines 412-415)."""
    with patch('app.services.processing_service.settings.DOCUMENT_FIELDS') as mock_fields:
        mock_fields.__getitem__.side_effect = KeyError("CRL")
        
        doc_input = DocumentInput(
            document_id="DOC_BAD",
            document_type="CRL",
            pages=["page1.png"]
        )
        
        # Act - will trigger exception in field groups check
        result, metrics = await _extract_data_for_document(
            job_id="JOB_BAD",
            doc_input=doc_input,
            base_path="/test/docs"
        )
        
        # Assert
        assert result is not None


@pytest.mark.asyncio
async def test_page_centric_with_separator_fallback(mock_settings, mock_logger):
    """Tests page-centric extraction when separator is not found (lines 212-214)."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        with patch('app.services.processing_service._get_page_prompt_template') as mock_template:
            # Template without separator - has only static placeholders that will be provided
            # This tests the fallback when separator is missing from config
            mock_template.return_value = "Extract {doc_type} {invoice_extractor_domain_context}"
            
            mock_llm.call_llm_with_parsing = AsyncMock(
                return_value=({"field1_Value": "data1"}, {"latency_ms": 50, "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "prompt_tokens_details": {}}}),
            )
            
            with patch('app.services.processing_service.create_extraction_model'):
                doc_input = DocumentInput(
                    document_id="DOC_SEP",
                    document_type="INVOICE",
                    pages=["page1.png"]
                )
                
                field_groups = {"totals": [{"name": "field1", "description": "test"}]}
                
                # Act - separator is missing, should use fallback (entire template as static)
                result, metrics, failures = await _extract_page_centric(
                    job_id="JOB_SEP",
                    doc_input=doc_input,
                    base_path="/test/docs",
                    field_groups=field_groups
                )
                
                # Assert - verify extraction completed despite missing separator
                # The warning is logged via the real logger (visible in captured output)
                assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_document_centric_with_separator_fallback(mock_settings, mock_logger):
    """Tests document-centric extraction when separator not found (lines 307-309)."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        with patch('app.services.processing_service._get_document_prompt_template') as mock_template:
            # Template without separator - must have expected format placeholders
            mock_template.return_value = "Extract {doc_type} context: {field_list_str}"
            
            mock_llm.call_llm_with_parsing = AsyncMock(
                return_value=({"field1_Value": "data1"}, {"latency_ms": 100, "token_usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30, "prompt_tokens_details": {}}})
            )
            
            with patch('app.services.processing_service.create_extraction_model'):
                doc_input = DocumentInput(
                    document_id="DOC_DSEP",
                    document_type="INVOICE",
                    pages=["page1.png"]
                )
                
                field_groups = {"totals": [{"name": "field1", "description": "test"}]}
                
                # Act
                result, metrics, failures = await _extract_document_centric(
                    job_id="JOB_DSEP",
                    doc_input=doc_input,
                    base_path="/test/docs",
                    field_groups=field_groups
                )
                
                # Assert
                assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_basemodel_response_dump(mock_settings, mock_logger):
    """Tests handling of BaseModel response with model_dump (lines 253-255)."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        # Create a Pydantic BaseModel response
        class TestResponse(BaseModel):
            field1_Value: str = "test_value"
        
        test_response = TestResponse()
        
        mock_llm.call_llm_with_parsing = AsyncMock(
            return_value=(test_response, {"latency_ms": 50, "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "prompt_tokens_details": {}}})
        )
        
        with patch('app.services.processing_service.create_extraction_model') as mock_create:
            mock_create.return_value = TestResponse
            
            doc_input = DocumentInput(
                document_id="DOC_BM",
                document_type="INVOICE",
                pages=["page1.png"]
            )
            
            field_groups = {"totals": [{"name": "field1", "description": "test"}]}
            
            # Act
            result, metrics, failures = await _extract_page_centric(
                job_id="JOB_BM",
                doc_input=doc_input,
                base_path="/test/docs",
                field_groups=field_groups
            )
            
            # Assert
            assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_empty_formatted_data_status(mock_settings, mock_logger):
    """Tests final status when formatted data is empty (lines 488-489)."""
    result = await _extract_data_for_document(
        job_id="JOB_EMPTY",
        doc_input=DocumentInput(
            document_id="DOC_EMPTY",
            document_type="UNSUPPORTED",
            pages=[]
        ),
        base_path="/test/docs"
    )
    
    # Assert - should return skipped status
    assert result[0].processing_status in ["Skipped", "Failed"]


@pytest.mark.asyncio
async def test_token_aggregation_with_details(mock_settings, mock_logger):
    """Tests token detail aggregation (lines 271-272, 377-378)."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        mock_llm.call_llm_with_parsing = AsyncMock(
            return_value=(
                {"field1_Value": "data1"},
                {
                    "latency_ms": 100,
                    "token_usage": {
                        "prompt_tokens": 50,
                        "completion_tokens": 25,
                        "total_tokens": 75,
                        "prompt_tokens_details": {
                            "text_tokens": 40,
                            "cached_tokens": 10,
                            "audio_tokens": 0
                        }
                    }
                }
            )
        )
        
        with patch('app.services.processing_service.create_extraction_model'):
            doc_input = DocumentInput(
                document_id="DOC_TOK",
                document_type="INVOICE",
                pages=["page1.png"]
            )
            
            field_groups = {"totals": [{"name": "field1", "description": "test"}]}
            
            # Act
            result, metrics, failures = await _extract_page_centric(
                job_id="JOB_TOK",
                doc_input=doc_input,
                base_path="/test/docs",
                field_groups=field_groups
            )
            
            # Assert
            assert isinstance(metrics, dict)
            assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_no_field_groups_found(mock_settings, mock_logger):
    """Tests when no field groups found for document type (lines 428-430)."""
    with patch('app.services.processing_service.settings.DOCUMENT_FIELDS') as mock_fields:
        # Return fields but with empty page_wise and document_wise
        mock_fields.get.return_value = {}
        
        doc_input = DocumentInput(
            document_id="DOC_NOGRP",
            document_type="CRL",
            pages=["page1.png"]
        )
        
        # Act
        result, metrics = await _extract_data_for_document(
            job_id="JOB_NOGRP",
            doc_input=doc_input,
            base_path="/test/docs"
        )
        
        # Assert
        assert result is not None


@pytest.mark.asyncio
async def test_extraction_with_llm_error_recovery(mock_settings, mock_logger):
    """Tests error tracking when LLM returns error (lines 257, 366-367)."""
    with patch('app.services.processing_service.llm_client') as mock_llm:
        # LLM returns error response (not raising exception)
        mock_llm.call_llm_with_parsing = AsyncMock(
            return_value=(
                {"error": "LLM parsing failed"},  # Error response, not BaseModel
                {"latency_ms": 100, "token_usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30, "prompt_tokens_details": {}}}
            )
        )
        
        with patch('app.services.processing_service.create_extraction_model'):
            doc_input = DocumentInput(
                document_id="DOC_ERR",
                document_type="INVOICE",
                pages=["page1.png"]
            )
            
            field_groups = {"totals": [{"name": "field1", "description": "test"}]}
            
            # Act
            result, metrics, failures = await _extract_page_centric(
                job_id="JOB_ERR",
                doc_input=doc_input,
                base_path="/test/docs",
                field_groups=field_groups
            )
            
            # Assert - should mark as having failures when LLM returns error
            assert failures[1] == True  # has_failures flag should be set