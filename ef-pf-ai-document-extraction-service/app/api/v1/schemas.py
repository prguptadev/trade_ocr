# app/api/v1/schemas.py
from pydantic import BaseModel, Field, create_model, constr, ConfigDict
from typing import Optional, List, Dict, Any, Type, Literal, Annotated

# --- Input Schemas ---

class ProcessFolderRequest(BaseModel):
    folder_path: str = Field(..., alias='docPath', description="The absolute path to the folder containing documents to process.")
    srn_no: str = Field(..., description="The unique id assigned to the case folder containing documents to process.")

    model_config = ConfigDict(populate_by_name=True)

class DocumentInput(BaseModel):
    """Schema for a single document within the input request."""
    document_id: str
    document_type: str
    pages: List[str]
    confidence_score: Optional[Any] = None

class PromptTokensDetails(BaseModel):
    """Schema for the detailed token usage in the prompt."""
    audio_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    text_tokens: Optional[int] = None
    image_tokens: Optional[int] = None

class TokenUsage(BaseModel):
    """Schema for the overall token usage."""
    completion_tokens: int
    prompt_tokens: int
    total_tokens: int
    completion_tokens_details: Optional[Any] = None
    prompt_tokens_details: Optional[PromptTokensDetails] = None

class ProcessingMetadata(BaseModel):
    """Schema for the processing metadata from the classification step."""
    ai_call_latency_ms: float
    token_usage: TokenUsage

class ProcessingRequest(BaseModel):
    """
    The main Pydantic model for the entire input JSON payload.
    This schema validates the structure provided in your instructions.
    """
    request: ProcessFolderRequest
    job_id: str
    documents: List[DocumentInput]
    processing_metadata: ProcessingMetadata


# --- Output Schemas ---

class ExtractedField(BaseModel):
    """Schema for a single extracted field in the output."""
    value: Optional[Any] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None

class DocumentResult(BaseModel):
    """Schema for the processing result of a single document."""
    document_id: str
    document_type: str
    pages: List[str]
    processing_status: str
    confidence_score: Optional[Any] = None
    extracted_data: Dict[str, ExtractedField] = {}
    error_message: Optional[str] = None

class ProcessingResponse(BaseModel):
    """The final JSON output response for a completed job."""
    # job_id: str
    # status: str
    results: List[DocumentResult]
    # Includes the original metadata for passthrough
    processing_metadata: ProcessingMetadata


# --- Internal Schemas ---

class JobStatus(BaseModel):
    """Defines the schema for a job's status check."""
    request: ProcessFolderRequest
    job_id: str
    status: str
    details: str
    progress_percent: float = 0.0
    result: Optional[ProcessingResponse] = None

def create_extraction_model(model_name: str, fields: List[Dict]) -> Type[BaseModel]:
    """
    Dynamically creates a FLAT Pydantic model with regex validation derived from the input JSON.
    """
    field_definitions = {}

    for field in fields:
        # Sanitize field name for the property key
        base_name = ''.join(c if c.isalnum() else '_' for c in field['name'])

        # Extract validation rules from the JSON
        regex_pattern = field.get("validation_regex", None)
        description = field.get("description", "")

        # Define the Value field with dynamic validation
        # We use Annotated validation via Field(pattern=...)
        field_definitions[f"{base_name}_Value"] = (
            Optional[str], 
            Field(
                default=None, 
                description=description,
                pattern=regex_pattern  # Applies regex if provided, else None
            )
        )

        # Define the Confidence field (kept standard)
        field_definitions[f"{base_name}_Confidence"] = (
            Optional[Literal['HIGH', 'MEDIUM', 'LOW', 'NOT_FOUND']], 
            Field(default=None)
        )
        
        # Add Reason field specifically for date_of_receipt_of_document
        if field['name'] == 'date_of_receipt_of_document':
            field_definitions[f"{base_name}_Reason"] = (
                Optional[str],
                Field(default=None, description="Reason for extraction")
            )

    return create_model(model_name, **field_definitions)