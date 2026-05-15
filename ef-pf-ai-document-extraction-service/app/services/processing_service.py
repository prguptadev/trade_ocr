import os
import json
import asyncio
from datetime import datetime
from fastapi import Request, Depends
from typing import Dict, Any, List, Coroutine, Union, Tuple, Iterable, Set, Optional
from pydantic import BaseModel

from opentelemetry import trace # Added this line

from app.core.config import settings
from app.middleware.tracing import _format_srn_as_trace_id
from app.core.logging_config import log
from app.services.llm_client import llm_client
from app.api.v1.schemas import (
    ProcessingRequest, DocumentInput, DocumentResult, JobStatus,
    ExtractedField, create_extraction_model, ProcessingResponse
)
from app.prompts import (  
    EXTRACTION_PROMPT_TEMPLATE_PAGE_CRL,          
    EXTRACTION_PROMPT_TEMPLATE_PAGE_INVOICE,      
    EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_CRL,      
    EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_INVOICE,  
    CRL_EXTRACTOR_DOMAIN_CONTEXT,                 
    INVOICE_EXTRACTOR_DOMAIN_CONTEXT,             
)
from app.core.config import settings


def _get_page_prompt_template(doc_type: str) -> str:  
    """
    Returns the page-wise extraction prompt template for the given document type.
    Defaults to the INVOICE template if an unknown type is encountered.
    """
    if doc_type == "CRL":
        return EXTRACTION_PROMPT_TEMPLATE_PAGE_CRL
    if doc_type == "INVOICE":
        return EXTRACTION_PROMPT_TEMPLATE_PAGE_INVOICE
    # Fallback: in case of unexpected doc_type, still use a valid template
    return EXTRACTION_PROMPT_TEMPLATE_PAGE_INVOICE


def _get_document_prompt_template(doc_type: str) -> str:  
    """
    Returns the document-wise extraction prompt template for the given document type.
    Defaults to the INVOICE template if an unknown type is encountered.
    """
    if doc_type == "CRL":
        return EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_CRL
    if doc_type == "INVOICE":
        return EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_INVOICE
    # Fallback: in case of unexpected doc_type, still use a valid template
    return EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_INVOICE

def _sanitize_base(name: str) -> str:
    """
    Must match how your pipeline stores extracted keys.
    """
    return "".join(c if c.isalnum() else "_" for c in name)


def _field_keys(field_name: str) -> Set[str]:
    base = _sanitize_base(field_name)
    return {f"{base}_Value", f"{base}_Confidence"}


def _collect_group_field_names(unique_fields: list) -> Set[str]:
    names = set()
    for f in unique_fields:
        if isinstance(f, dict) and f.get("name"):
            names.add(f["name"])
    return names


def _filter_prev_json_for_group(
    extracted_json: Dict[str, Any],
    unique_fields: list,
    always_include_field_names: Iterable[str] = (),
    keep_non_null_only: bool = True,
) -> Dict[str, Any]:
    """
    Returns a reduced previous JSON containing:
    - Keys for fields in the current group
    - Keys for fields in an allowlist (cross-group dependencies)
    """
    names = _collect_group_field_names(unique_fields)
    names.update(always_include_field_names or [])

    keep_keys: Set[str] = set()
    for n in names:
        keep_keys |= _field_keys(n)

    filtered: Dict[str, Any] = {}
    for k in keep_keys:
        if k in extracted_json:
            v = extracted_json[k]
            if keep_non_null_only and (v is None or v == "" or v == [] or v == {}):
                continue
            filtered[k] = v


    return filtered

async def _extract_data_for_field_group(
    group_context: str,
    prompt: str,
    full_page_paths: List[str],
    extraction_schema: BaseModel,
    group_name: str,
    job_id: Optional[str] = None,
    request: Optional[Request] = None # Made optional
) -> Tuple[Union[Dict[str, Any], None], Dict]:
    """
    Calls the LLM to extract data for a single field group and returns the result and metrics.
    Returns (None, empty dict) on failure.
    """
    log.info(f"[{group_context}] Starting extraction for field group {group_name}.")
    response_data, metrics = await llm_client.call_llm_with_parsing(
            prompt,
            full_page_paths,
            extraction_schema,
            group_context,
            srn_no=job_id,
            request=request,
            explicit_root_trace_id=(
                format(trace.get_current_span().context.trace_id, "032x")
                if trace.get_current_span().context.is_valid
                else None
            )
        )    
    if isinstance(response_data, BaseModel):
        log.info(f"[{group_context}] Successfully extracted data for group {group_name}.")
        return response_data.model_dump(), metrics

    error_dict = {'group': group_name}

    error_msg = "Unknown failure"
    error_details = "No details available."
    if isinstance(response_data, dict):
        error_msg = response_data.get('error', error_msg)
        error_details = response_data.get('raw_response', error_details)
        error_dict.update({'error': error_msg})

    log.error(f"[{group_context}] FAILED to extract data for group {group_name}. Error: {error_msg}. Details: {str(error_details)[:200]}...")
    return response_data, metrics

async def _extract_page_centric(
    job_id: str,
    doc_input: DocumentInput,
    base_path: str,
    field_groups: Dict,
    fastapi_request: Optional[Request] = None
) -> Tuple[Dict[str, Any], Dict, List[Union[str, bool]]]:
    """
    Extracts data for a single document by iteratively processing it page by page
    and group by group to keep response schemas small.
    """
    context_prefix = f"Srn No.:{job_id}|Doc:{doc_input.document_id}|Strategy:Page"
    extracted_json = {}
    document_has_failures = False
    page_error_message = ""
    error_message = "Unknown Failure"

    ALWAYS_INCLUDE_FIELDS = {
    # CRL
    "applicant_name",
    "beneficiary_name",

    # Invoice
    "buyer_name",
    "seller_name",
}
    
    total_latency = 0.0
    total_completion_tokens = 0
    total_prompt_tokens = 0
    total_total_tokens = 0
    total_text_tokens = 0
    total_cached_tokens = 0

    page_prompt_template = _get_page_prompt_template(doc_input.document_type)  

    # Outer loop for each page
    for i, page in enumerate(doc_input.pages):
        page_context = f"{context_prefix}|Page:{page}"
        full_page_path = [os.path.join(base_path, page)]
        log.info(f"Processing page {i+1}/{len(doc_input.pages)}: {page}")

        if i > 0:
            await asyncio.sleep(1.0)  # 1 second delay between pages

        # Inner loop for each field group
        for group_name, fields_to_extract in field_groups.items():
            group_context = f"{page_context}|Group:{group_name}"
            
            # 1. Create a small schema for just this group
            unique_fields = list({field['name']: field for field in fields_to_extract}.values())
            if not unique_fields:
                continue
            
            extraction_schema = create_extraction_model(f"{doc_input.document_type}{group_name}PageWise", unique_fields)
            log.info(f"[{group_context}] Generated schema for group {group_name}")

            prev_for_group = _filter_prev_json_for_group(
                extracted_json=extracted_json,
                unique_fields=unique_fields,
                always_include_field_names=ALWAYS_INCLUDE_FIELDS,
                keep_non_null_only=True,
            )
            
            prev_json_str = json.dumps(prev_for_group, indent=2, ensure_ascii=False)
            full_prev_str = json.dumps(extracted_json, ensure_ascii=False)
            
            log.info(
                f"[{group_context}] prev_json_chars full={len(full_prev_str)} "
                f"filtered={len(prev_json_str)} "
                f"(group={group_name}, fields={len(unique_fields)})"
            )
            
            # Build prompt with static/dynamic split for caching
            SEPARATOR = "<<<DYNAMIC_SECTION_START>>>"
            
            # Datetime logic for current date, month and year
            now = datetime.now()
            today = datetime.now().date()
            current_year = now.year
            current_month = now.month
            current_day = now.day

            if SEPARATOR in page_prompt_template:
                static_template, dynamic_template = page_prompt_template.split(SEPARATOR)
            else:
                log.warning(f"[{group_context}] Separator not found, caching disabled")
                static_template = page_prompt_template
                dynamic_template = ""
            
            # Build static part (constant for all pages of this doc type)
            if doc_input.document_type == "CRL":
                static_prompt = static_template.format(
                    doc_type=doc_input.document_type,
                    crl_extractor_domain_context=CRL_EXTRACTOR_DOMAIN_CONTEXT,
                    today=today
                )
            else:  # INVOICE
                static_prompt = static_template.format(
                    doc_type=doc_input.document_type,
                    invoice_extractor_domain_context=INVOICE_EXTRACTOR_DOMAIN_CONTEXT,
                    current_year=current_year,
                    current_month=current_month,
                    current_day=current_day,
                )
            
            if doc_input.document_type == "CRL":
                dynamic_suffix = dynamic_template.format(
                    previously_extracted_json=prev_json_str,
                    today=today
                )
            else:  # INVOICE
                dynamic_suffix = dynamic_template.format(
                    previously_extracted_json=prev_json_str,
                    current_year=current_year,
                    current_month=current_month,
                    current_day=current_day,
                )
            
            # Combine
            prompt = static_prompt + SEPARATOR + dynamic_suffix

            # 3. Call the LLM for this specific group and page
            response_data, metrics = await llm_client.call_llm_with_parsing(
                prompt, 
                full_page_path, 
                extraction_schema, 
                group_context, 
                srn_no=job_id, 
                request=fastapi_request,
                explicit_root_trace_id=_format_srn_as_trace_id(job_id) if job_id else None
            )
            cached = metrics.get("token_usage", {}).get("prompt_tokens_details", {})
            cached_val = getattr(cached, "cached_tokens", None) if cached else None
            log.info(f"[{group_context}] CACHE: {cached_val}")
            # 4. Update the overall extracted_json with the result from this group
            if isinstance(response_data, BaseModel):
                response_dict = response_data.model_dump() 
                extracted_json.update(response_data.model_dump())
                log.info(f"[{group_context}] Successfully extracted and merged data.")
            else:
                document_has_failures = True
                page_error_message += f'{group_name} - {response_data.get("error", error_message)}, '
                log.error(f"[{group_context}] Failed to extract data for this group.")
                log.info(f"[{group_context}] Successfully merged the failed data.")                

            # 5. Aggregate metrics
            if metrics:
                total_latency += metrics.get("latency_ms", 0)
                token_usage = metrics.get("token_usage", {})
                total_completion_tokens += token_usage.get("completion_tokens", 0) or 0
                total_prompt_tokens += token_usage.get("prompt_tokens", 0) or 0
                total_total_tokens += token_usage.get("total_tokens", 0) or 0
                ptd = token_usage.get("prompt_tokens_details", {})
                if ptd:
                    total_text_tokens += getattr(ptd, "text_tokens", 0) or 0
                    total_cached_tokens += getattr(ptd, "cached_tokens", 0) or 0

    aggregated_metrics = {
        "ai_call_latency_ms": total_latency,
        "token_usage": {
            "completion_tokens": total_completion_tokens,
            "prompt_tokens": total_prompt_tokens,
            "total_tokens": total_total_tokens,
            "prompt_tokens_details": {"text_tokens": total_text_tokens, "cached_tokens": total_cached_tokens}
        }
    }
    return extracted_json, aggregated_metrics, [page_error_message, document_has_failures]

async def _extract_document_centric(
    job_id: str, 
    doc_input: DocumentInput, 
    base_path: str, 
    field_groups: Dict,
    fastapi_request: Optional[Request] = None
) -> Tuple[Dict[str, Any], Dict, List[Union[str,  bool]]]:
    context_prefix = f"Srn No.:{job_id}|Doc:{doc_input.document_id}|Strategy:Document"
    full_page_paths = [os.path.join(base_path, page) for page in doc_input.pages]
    
    document_prompt_template = _get_document_prompt_template(doc_input.document_type)  

    SEPARATOR = "<<<DYNAMIC_SECTION_START>>>"
    
    # Pre-split template once for all groups (static part is identical across groups)
    if SEPARATOR in document_prompt_template:
        static_template, dynamic_template = document_prompt_template.split(SEPARATOR)
        
        # Build static part once (same for all groups of this doc type)
        if doc_input.document_type == "CRL":
            static_prompt = static_template  # Already has CRL_EXTRACTOR_DOMAIN_CONTEXT
        else:  # INVOICE
            static_prompt = static_template  # Already has INVOICE_EXTRACTOR_DOMAIN_CONTEXT
        
    else:
        # Fallback if separator not found
        log.warning(f"[{context_prefix}] Separator not found in document template, caching disabled")
        static_prompt = None
        dynamic_template = document_prompt_template
    
    extraction_tasks: List[Coroutine] = []
    
    for group_name, fields_to_extract in field_groups.items():
        unique_fields = list({field['name']: field for field in fields_to_extract}.values())
        group_context = f"{context_prefix}({group_name})"
        
        field_list_str = "\n".join([f"- {f['name']}: {f['description']}" for f in unique_fields])
        extraction_schema = create_extraction_model(
            f"{doc_input.document_type}{group_name}", unique_fields
        )
        
        # Build prompt with static/dynamic split
        if static_prompt is not None:
            # Dynamic part (changes per group due to field_list_str)
            dynamic_suffix = dynamic_template.format(
                doc_type=doc_input.document_type,
                case_id=doc_input.document_id,
                num_pages=len(full_page_paths),
                field_list_str=field_list_str
            )
            
            prompt = static_prompt + SEPARATOR + dynamic_suffix
            
        else:
            # Fallback (no caching)
            prompt = dynamic_template.format(
                doc_type=doc_input.document_type,
                case_id=doc_input.document_id,
                num_pages=len(full_page_paths),
                field_list_str=field_list_str
            )
            
            log.info(f"[{group_context}] total_prompt_chars={len(prompt)} (no cache)")
        
        task = _extract_data_for_field_group(
            group_context, prompt, full_page_paths, extraction_schema, group_name, job_id, fastapi_request
        )
        extraction_tasks.append(task)
    
    group_results: List[Tuple[Union[Dict[str, Any], None], Dict]] = await asyncio.gather(*extraction_tasks)
    
    final_extraction_results = {}
    has_failures = False
    total_latency = 0.0
    total_completion_tokens = 0
    total_prompt_tokens = 0
    total_total_tokens = 0
    total_text_tokens = 0
    total_cached_tokens = 0
    docs_error_message = ""

    for (result, metrics) in group_results:
        if result is not None:
            final_extraction_results.update(result)
        else:
            has_failures = True
            docs_error_message += f'{metrics["group"]} - {metrics["error"]}, '

        if metrics:
            total_latency += metrics.get("latency_ms", 0)
            token_usage = metrics.get("token_usage", {})
            total_completion_tokens += token_usage.get("completion_tokens", 0) or 0
            total_prompt_tokens += token_usage.get("prompt_tokens", 0) or 0
            total_total_tokens += token_usage.get("total_tokens", 0) or 0
            ptd = token_usage.get("prompt_tokens_details", {})
            if ptd:
                total_text_tokens += getattr(ptd, "text_tokens", 0) or 0
                total_cached_tokens += getattr(ptd, "cached_tokens", 0) or 0

    aggregated_metrics = {
        "ai_call_latency_ms": total_latency,
        "token_usage": {
            "completion_tokens": total_completion_tokens,
            "prompt_tokens": total_prompt_tokens,
            "total_tokens": total_total_tokens,
            "prompt_tokens_details": {"text_tokens": total_text_tokens, "cached_tokens": total_cached_tokens}
        }
    }

    return final_extraction_results, aggregated_metrics, [docs_error_message, has_failures]

async def _extract_data_for_document(
    job_id: str, doc_input: DocumentInput, base_path: str, fastapi_request: Optional[Request] = None
) -> Tuple[DocumentResult, Dict]:
    context_prefix = f"Srn No.:{job_id}|Doc:{doc_input.document_id}"
    
    supported_doctypes = settings.DOCUMENT_FIELDS.keys()
    if doc_input.document_type not in supported_doctypes:
        info_msg = f"Skipping document with type '{doc_input.document_type}' as it is not supported."
        log.info(f"{context_prefix}: {info_msg}")
        return DocumentResult(
            document_id=doc_input.document_id,
            document_type=doc_input.document_type,
            pages=doc_input.pages,
            processing_status="Skipped",
            confidence_score=doc_input.confidence_score,
            extracted_data={},
        ), {}

    field_groups = settings.DOCUMENT_FIELDS.get(doc_input.document_type)
    if not isinstance(field_groups, dict):
        error_msg = f"Field groups for doc type '{doc_input.document_type}' are malformed."
        log.error(f"[{context_prefix}] {error_msg}")
        return DocumentResult(
            document_id=doc_input.document_id,
            document_type=doc_input.document_type,
            pages=doc_input.pages,
            processing_status="Failed",
            error_message=error_msg,
            confidence_score=doc_input.confidence_score,
            extracted_data={},
        ), {}

    page_centric_groups = field_groups.get("page_wise", {})
    document_centric_groups = field_groups.get("document_wise", {})

    if not page_centric_groups and not document_centric_groups:
        log.warning(f"[{context_prefix}] No page-wise or document-wise field groups found for document type '{doc_input.document_type}'.")
        return DocumentResult(
            document_id=doc_input.document_id,
            document_type=doc_input.document_type,
            pages=doc_input.pages,
            processing_status="Skipped",
            confidence_score=doc_input.confidence_score,
            extracted_data={},
            error_message="No fields configured for this document type."
        ), {}

    doc_centric_results, doc_centric_metrics, doc_has_failures = {}, {}, []
    if document_centric_groups:
        log.info(f"Found {len(document_centric_groups)} document-wise field groups.")
        doc_centric_results, doc_centric_metrics, doc_has_failures = await _extract_document_centric(
            job_id, doc_input, base_path, document_centric_groups, fastapi_request
        )

    page_centric_results, page_centric_metrics, page_has_failures = {}, {}, []
    if page_centric_groups:
        log.info(f"Found {len(page_centric_groups)} page-wise field groups.")
        page_centric_results, page_centric_metrics, page_has_failures = await _extract_page_centric(
            job_id, doc_input, base_path, page_centric_groups, fastapi_request
        )
    
    final_extraction_results = {**doc_centric_results, **page_centric_results}
    document_has_failures = doc_has_failures[1] or page_has_failures[1]
    
    total_latency = doc_centric_metrics.get("ai_call_latency_ms", 0) + page_centric_metrics.get("ai_call_latency_ms", 0)
    total_completion_tokens = doc_centric_metrics.get("token_usage", {}).get("completion_tokens", 0) + page_centric_metrics.get("token_usage", {}).get("completion_tokens", 0)
    total_prompt_tokens = doc_centric_metrics.get("token_usage", {}).get("prompt_tokens", 0) + page_centric_metrics.get("token_usage", {}).get("prompt_tokens", 0)
    total_total_tokens = doc_centric_metrics.get("token_usage", {}).get("total_tokens", 0) + page_centric_metrics.get("token_usage", {}).get("total_tokens", 0)
    total_text_tokens = doc_centric_metrics.get("token_usage", {}).get("prompt_tokens_details", {}).get("text_tokens", 0) + page_centric_metrics.get("token_usage", {}).get("prompt_tokens_details", {}).get("text_tokens", 0)
    total_cached_tokens = (doc_centric_metrics.get("token_usage", {}).get("prompt_tokens_details", {}).get("cached_tokens", 0) + page_centric_metrics.get("token_usage", {}).get("prompt_tokens_details", {}).get("cached_tokens", 0))

    formatted_data = {}
    
    all_fields_for_type = []
    for group in page_centric_groups.values():
        all_fields_for_type.extend(group)
    for group in document_centric_groups.values():
        all_fields_for_type.extend(group)

    for field in all_fields_for_type:
        original_field_name = field["name"]
        sanitized_base_name = "".join(c if c.isalnum() else "_" for c in original_field_name)
        value_key = f"{sanitized_base_name}_Value"
        confidence_key = f"{sanitized_base_name}_Confidence"

        if value_key in final_extraction_results:
            raw_confidence = final_extraction_results.get(confidence_key)
            numeric_confidence = settings.CONFIDENCE_SCORE_MAPPING.get(raw_confidence, settings.DEFAULT_CONFIDENCE)
            
            # Only extract reason for date_of_receipt_of_document
            reason = None
            if original_field_name == "date_of_receipt_of_document":
                reason_key = f"{sanitized_base_name}_Reason"
                reason = final_extraction_results.get(reason_key)
            
            formatted_data[original_field_name] = ExtractedField(
                value=final_extraction_results.get(value_key),
                confidence=numeric_confidence,
                reason=reason,
            )

    final_status = "Success"
    if document_has_failures and formatted_data:
        final_status = "Partial Success"
    elif not formatted_data:
        final_status = "Failed"

    aggregated_token_usage = {
        "completion_tokens": total_completion_tokens,
        "prompt_tokens": total_prompt_tokens,
        "total_tokens": total_total_tokens,
        "prompt_tokens_details": {"text_tokens": total_text_tokens, "cached_tokens": total_cached_tokens}
    }

    aggregated_processing_metadata = {
        "ai_call_latency_ms": total_latency,
        "token_usage": aggregated_token_usage
    }

    final_error_message = f"The following pages or field groups failed extraction: {doc_has_failures[0]}{page_has_failures[0]}"

    return DocumentResult(
        document_id=doc_input.document_id,
        document_type=doc_input.document_type,
        pages=doc_input.pages,
        processing_status=final_status,
        confidence_score=doc_input.confidence_score,
        extracted_data=formatted_data,
        error_message=final_error_message if document_has_failures else None
    ), aggregated_processing_metadata


async def process_documents(request: ProcessingRequest, file_path: str, fastapi_request: Optional[Request] = None) -> JobStatus:
    job_id = request.request.srn_no
    log.info(f"job_id: ${job_id}")
    
    job = JobStatus(
        request=request.request,
        job_id=job_id,
        status="Processing",
        details="Starting document processing..."
    )
    log.info(f"Starting processing for job {job_id} with {len(request.documents)} documents.")
    
    tasks = [
        _extract_data_for_document(job_id, doc, file_path, fastapi_request)
        for doc in request.documents
    ]
    
    results: List[DocumentResult] = []
    total_docs = len(tasks)
    docs_processed = 0

    total_latency = 0.0
    total_completion_tokens = 0
    total_prompt_tokens = 0
    total_total_tokens = 0
    total_text_tokens = 0
    total_cached_tokens = 0

    for future in asyncio.as_completed(tasks):
        try:
            result, metrics = await future
            results.append(result)

            token_usage = metrics.get("token_usage", {})

            total_latency += metrics.get("ai_call_latency_ms", 0)
            total_completion_tokens += token_usage.get("completion_tokens", 0)
            total_prompt_tokens += token_usage.get("prompt_tokens", 0)
            total_total_tokens += token_usage.get("total_tokens", 0)
            
            ptd = token_usage.get("prompt_tokens_details", {})
            if ptd:
                total_text_tokens += getattr(ptd, "text_tokens", 0)

        except Exception as e:
            log.exception(f"A critical error occurred in a processing task for job {job_id}: {e}")
            job.status = "Failed"
            job.details = f"A critical error occurred: {e}"
            return job
        finally:
            docs_processed += 1
            job.progress_percent = (docs_processed / total_docs) * 100
            job.details = f"Processed {docs_processed}/{total_docs} documents."
            log.info(f"Job {job_id}: {job.details}")
            
    job.status = "Completed"

    aggregated_token_usage = {
        "completion_tokens": total_completion_tokens,
        "prompt_tokens": total_prompt_tokens,
        "total_tokens": total_total_tokens,
        "prompt_tokens_details": {"text_tokens": total_text_tokens, "cached_tokens": total_cached_tokens}
    }

    aggregated_processing_metadata = {
        "ai_call_latency_ms": total_latency,
        "token_usage": aggregated_token_usage
    }

    job.result = ProcessingResponse(
        results=results,
        processing_metadata=aggregated_processing_metadata
    )
    
    # Final check for CRL documents and log specific fields
    for res in results:
        if res.document_type == "CRL":
            extracted = res.extracted_data
            d_acc = extracted.get("debit_account_no")
            r_curr = extracted.get("remittance_currency")
            r_amt = extracted.get("remittance_amount")

            d_acc_val = d_acc.value if d_acc else None
            r_curr_val = r_curr.value if r_curr else None
            r_amt_val = r_amt.value if r_amt else None

            log.info(f"SRN: {job_id} | CRL Data - debit_account_no: {d_acc_val}, remittance_currency: {r_curr_val}, remittance_amount: {r_amt_val}")
            
    log_extra = {"job_id": job_id}
    log.info(f"SRN: {job_id} | Overall Latency: {total_latency}ms", extra=log_extra)
    log.info(f"SRN: {job_id} | Input Tokens: {total_prompt_tokens}", extra=log_extra)
    log.info(f"SRN: {job_id} | Output Tokens: {total_completion_tokens}", extra=log_extra)
    log.info(f"SRN: {job_id} | Total Tokens: {total_total_tokens}", extra=log_extra)
 
    log.info(f"Job {job_id} completed successfully.")
    
    return job
