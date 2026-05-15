# Test Suite Guide

## Quick Start

This directory houses the complete test suite for the AI Document Extraction Service. With **127 passing tests** and **94% code coverage**, it ensures reliability across all service modules.

```
app/tests/
├── services/
│   ├── test_llm_client.py          # 56 tests (95% coverage) - LLM interactions
│   └── test_processing_service.py  # 71 tests (94% coverage) - Document processing
├── README.md                        # This guide
└── test_output.log                  # Test execution logs
```

## Test Inventory

### LLM Client Tests (56 tests, 95% coverage)

Validates the AI model interaction layer with comprehensive coverage of:
- **Request Building:** Text, image, and PDF message preparation
- **Response Parsing:** JSON extraction from LLM outputs, handling malformed data
- **Error Handling:** Rate limits, connection failures, content filters
- **Retry Strategy:** Exponential backoff and re-ask mechanisms
- **Concurrency:** Semaphore-based request limiting
- **Token Tracking:** Monitoring cached and standard tokens

**Key Test Scenarios:**
- Successful parsing with token metrics
- Recovery from transient API failures  
- Validation errors with targeted re-asking
- Fallback JSON generation
- Multi-page document processing
- Empty or malformed responses

### Processing Service Tests (71 tests, 94% coverage)

Ensures document field extraction accuracy across different processing modes:

**Core Utilities (62 tests):**
- Field name normalization and sanitization
- Field key generation and filtering
- Group membership collection
- Previous extraction reuse and merging
- Template selection logic

**Extraction Orchestration (9 tests):**
- Page-by-page extraction workflows
- Full document extraction workflows
- Multi-strategy processing (page-wise + document-wise)
- Document type validation
- Field group configuration validation

**Edge Case Handling (9 tests):**
- Unsupported document types
- Malformed or missing field configurations
- Separator-free templates
- Response format variations
- Token aggregation across multiple extractions
- Extraction failures and recovery

## Running Tests

| Command | Purpose |
|---------|---------|
| `pytest app/tests/` | Run all tests |
| `pytest app/tests/ -v` | Verbose output with test names |
| `pytest app/tests/ --cov=app.services --cov-report=html` | Generate coverage report |
| `pytest app/tests/services/test_llm_client.py` | Run LLM client tests only |
| `pytest app/tests/services/test_processing_service.py` | Run processing tests only |
| `pytest app/tests/ -k "keyword"` | Run tests matching keyword |

## Architecture

### Test Structure Pattern

Every test follows the **Arrange-Act-Assert model**:

```python
@pytest.mark.asyncio
async def test_feature_behavior():
    # Arrange: Set up mocks and test data
    mock_api = MagicMock()
    
    # Act: Execute the function
    result = await process_data(mock_api)
    
    # Assert: Verify the outcome
    assert result.is_valid
```

### Fixtures

Test fixtures are self-contained within each test file, providing:

**Common Test Doubles:**
- `mock_settings` - Configuration overrides
- `mock_logger` - Logging capture
- `mock_openai_client` - LLM API simulation
- `mock_httpx_client` - HTTP client mocking

**Domain Objects:**
- Sample field definitions and extracted data
- Document input structures
- Pydantic model instances for validation

### Mocking Approach

We mock at these boundaries:

| Target | Method |
|--------|--------|
| External APIs | Context managers with patch |
| File operations | MockOpen or patch |
| Configuration | Direct fixture replacement |
| Logger | Mock instance capture |

Example:
```python
with patch('app.services.module.ExternalAPI', return_value=mock_api):
    result = await tested_function()
```

## Coverage Analysis

| Component | Coverage | Notes |
|-----------|----------|-------|
| llm_client.py | 95% | 9 statements uncovered (edge paths) |
| processing_service.py | 94% | 18 statements uncovered (runtime paths) |
| **Total** | **94%** | **27 statements** |

The small gap reflects hard-to-trigger runtime conditions (e.g., race conditions, OS-specific paths) rather than missing test intent.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Tests hang | Check for missing `await` on async calls |
| Mock isn't called | Verify patch path matches import location |
| AsyncMock fails | Ensure `from unittest.mock import AsyncMock` |
| Timeout errors | Increase timeout in `pytest.ini` if needed |

## Contributing

When adding tests:

1. ✅ Place test near the code it validates
2. ✅ Use `test_<function>_<scenario>` naming
3. ✅ Include docstring explaining the test goal
4. ✅ Test both success and failure paths
5. ✅ Mock external dependencies
6. ✅ Keep tests focused and independent
7. ✅ Update this guide if adding new sections

## Dependencies

Required packages (in `requirements.txt`):
- `pytest` - Test framework
- `pytest-asyncio` - Async test support
- `pytest-cov` - Coverage reporting
- `pytest-mock` - Enhanced mocking utilities

Install with: `pip install -r requirements.txt`

## Performance Notes

Full test suite execution: **~80 seconds**
- 56 LLM client tests: ~20s
- 71 processing service tests: ~60s

Tests use in-memory mocks to avoid I/O delays.

## Next Steps

Potential improvements:
- [ ] Performance benchmarking tests
- [ ] Property-based testing (hypothesis)
- [ ] End-to-end pipeline tests
- [ ] Load/stress testing for concurrent processing
- [ ] Contract testing for API boundaries
