# app/services/llm_client.py
import httpx
import asyncio
import base64
import time
import datetime
import re
import json5
import io
import uuid
import random

from fastapi import Request, Depends
from typing import List, Dict, Type, Union, Optional, Tuple
from openai import AsyncOpenAI, APIStatusError, APIConnectionError, RateLimitError, ContentFilterFinishReasonError
from pydantic import BaseModel, ValidationError
from PIL import Image
from app.core.logging_config import log
from app.utils.helpers import get_mime_type, process_pdf_to_images, convert_image_to_base64_string
from app.core.config import settings
from app.utils.storage import get_file_bytes

class LLMClient:
    """A thread-safe, async client for the LLM API with self-correction via targeted re-asks."""
    def __init__(self):
        http_client = httpx.AsyncClient(http2=True, verify=False, timeout=settings.API_TIMEOUT)
        self._client = AsyncOpenAI(
            api_key=settings.API_KEY,
            base_url=settings.API_BASE_URL,
            max_retries=0,
            http_client=http_client
        )
        self._semaphore = asyncio.Semaphore(settings.API_CONCURRENCY_LIMIT)
        log.info(f"Initialized LLMClient with concurrency limit of {settings.API_CONCURRENCY_LIMIT}.")

    def _prepare_request_messages(self, prompt_text: str, document_files: List[str]) -> List[Dict]:
        """
        Prepares the 'messages' payload for a multimodal API, supporting images and PDFs.
        
        PDFs are converted page-by-page into images and included in the request.
        """
        content_parts = [{"type": "text", "text": prompt_text}]
        for file_path in document_files:
            log.info(f"Processing file: {file_path}")
            try:
                mime_type = get_mime_type(file_path)

                if mime_type.startswith("image/"):
                    image_bytes = get_file_bytes(file_path)

                    with Image.open(io.BytesIO(image_bytes)) as image:
                        # Convert to RGB to ensure compatibility and remove alpha channel
                        rgb_image = image.convert("RGB")
                        log.info(f"Image pre-processing complete for {file_path}")
                        
                        base64_string = convert_image_to_base64_string(rgb_image, format="png")
                        log.info(f"Encoded {file_path} to Base64 string (MIME type: {mime_type})")
                        
                        content_parts.append({
                            "type": "image_url",
                            "image_url": f"data:image/png;base64,{base64_string}"
                        })

                elif mime_type == 'application/pdf':
                    log.info(f"Detected PDF file: {file_path}. Converting pages to images.")
                    file_content = get_file_bytes(file_path)
                    images_data = process_pdf_to_images(file_content)
                    
                    for img_data in images_data:
                        img_obj = img_data["image"]
                        # We use PNG for lossless quality, which is good for text
                        img_base64 = convert_image_to_base64_string(img_obj, format="png")
                        
                        content_parts.append({
                            "type": "image_url",
                            "image_url": f"data:image/png;base64,{img_base64}"
                        })
                    log.info(f"Successfully added {len(images_data)} pages from {file_path}.")

            except Exception as e:
                log.error(f"Failed to process file {file_path}: {e}", exc_info=True)
                continue
        
        log.info(f"Total content parts prepared: {len(content_parts)}")
        return [{"role": "user", "content": content_parts}]

    def _extract_json_from_text(self, text: str) -> Optional[dict]:
        """Finds and parses a JSON object from a string that might contain extra text."""
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            json_str = match.group(0)
            try:
                return json5.loads(json_str)
            except Exception:
                return None
        return None

    async def _attempt_targeted_reask(
        self, 
        original_prompt: str, 
        document_files: List[str], 
        response_schema: Type[BaseModel], 
        failure_reason: str, 
        context: str, 
        root_trace_id: str, # Standardized name
        srn_no: Optional[str] = None, 
        fallback_json: Optional[Dict] = None
    ) -> Union[BaseModel, Dict]:
        """Attempts to correct a failed extraction by re-issuing the original prompt with a warning."""
        log.warning(f"[{context}] Initiating targeted re-ask strategy due to parsing failure.")
        chat_id = "N/A"
        reask_preamble = (
            "Your previous attempt to generate a JSON response failed due to a formatting or validation error. "
            f"The error was: '{failure_reason[:500]}'.\n\n"
            "**IMPORTANT**: You MUST strictly adhere to the requested JSON schema. Provide ONLY the JSON object as a response, "
            "with no additional text, explanations, or apologies before or after the JSON structure.\n\n"
            "Now, please try the original request again:\n"
            "--- ORIGINAL REQUEST ---\n"
        )
        reask_prompt = reask_preamble + original_prompt
        
        best_effort_json = fallback_json

        for attempt in range(settings.JSON_CORRECTION_ATTEMPTS):
            reask_context = f"{context}-Reask-{attempt+1}"
            log.info(f"[{context}] Targeted re-ask attempt {attempt + 1}/{settings.JSON_CORRECTION_ATTEMPTS}.")
            span_id = uuid.uuid4().hex[:16]
            # Use the root_trace_id passed from the initial call
            traceparent = f"00-{root_trace_id}-{span_id}-01"

            headers = {"traceparent": traceparent}
            log.info(f"[{context}] Root Trace: {root_trace_id} | Span: {span_id} | Traceparent: {traceparent}")

            try:
                reask_messages = self._prepare_request_messages(reask_prompt, document_files)
                start_time = time.time()
                response = await self._client.chat.completions.parse (
                    model=settings.API_MODEL, 
                    messages=reask_messages, 
                    response_format=response_schema, 
                    temperature=0.03,
                    top_p=0.97,
                    reasoning_effort="disable",
                    extra_headers=headers,
                    max_completion_tokens=settings.MAX_COMPLETION_TOKENS,
                    max_tokens = settings.MAX_COMPLETION_TOKENS
                )
                duration_ms = (time.time() - start_time) * 1000
                token_usage = {}
                if response.usage:
                    token_usage = {
                        "completion_tokens": getattr(response.usage, 'completion_tokens', 0) or 0,
                        "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0) or 0,
                        "total_tokens": getattr(response.usage, 'total_tokens', 0) or 0,
                        "prompt_tokens_details": getattr(response.usage, "prompt_tokens_details", None) or {}
                    }
                else:
                    token_usage = {
                        "completion_tokens": 0,
                        "prompt_tokens": 0,
                        "total_tokens": 0,
                        "prompt_tokens_details": {}
                    }
                chat_id = response.id if response.id else "N/A"                
                metrics = {
                    "latency_ms": duration_ms,
                    "token_usage": token_usage,
                }
                log.info(
                    f"[{reask_context}] Targeted re-ask successful."
                    f"[{reask_context}] LLM call successful. [{duration_ms:.2f}ms] [NEEV CHAT ID:{chat_id}]"
                    f"[{reask_context}]Tokens: Prompt={token_usage.get('prompt_tokens')}, Completion={token_usage.get('completion_tokens')}, Total={token_usage.get('total_tokens')}.")

                return response.choices[0].message.parsed, metrics

            except ValidationError as e:
                log.warning(f"[{reask_context}] [NEEV CHAT ID:{chat_id}] Targeted re-ask attempt {attempt + 1} failed validation: {e}")
                
                # Attempt to salvage JSON from the failed retry
                raw_output = str(e.llm_output) if hasattr(e, 'llm_output') else str(e)
                recovered = self._extract_json_from_text(raw_output)
                if recovered:
                    log.info(f"[{reask_context}] Recovered partial JSON from validation failure.")
                    best_effort_json = recovered
                
                await asyncio.sleep(1.5)

            except Exception as e:
                log.error(f"[{reask_context}] [NEEV CHAT ID:{chat_id}] Targeted re-ask attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(1.5)

        log.error(f"[{context}] All targeted re-ask attempts failed.")
        
        if best_effort_json:
            log.warning(f"[{context}] Returning best-effort (unvalidated) JSON after re-ask failure.")
            # Construct the model without validation
            try:
                partial_model = response_schema.model_construct(**best_effort_json)
                return partial_model, {}
            except Exception as e:
                log.error(f"[{context}] Failed to construct partial model: {e}")

        return {"error": "Targeted re-ask failed after all attempts.", "raw_response": failure_reason}, {}

    async def call_llm_with_parsing(
        self, 
        prompt_text: str, 
        document_files: List[str], 
        response_schema: Type[BaseModel], 
        context: str, 
        srn_no: Optional[str] = None,
        request: Optional[Request] = None, # Made optional
        explicit_root_trace_id: Optional[str] = None, # New parameter
    ) -> Tuple[Union[BaseModel, Dict], Dict]:
        """
        Calls the LLM, parses response, and returns tuple:
        (parsed_data, metrics_dict)
        metrics_dict contains: latency_ms, completion_tokens, prompt_tokens, total_tokens, plus nested prompt tokens details if any.
        """
        log.info(f"[{context}] Waiting for semaphore. Slots available before acquire: {self._semaphore._value}")
        chat_id = "N/A"       
        async with self._semaphore:
            log.info(f"[{context}] Acquired semaphore. Slots available after acquire: {self._semaphore._value}")            
            messages = self._prepare_request_messages(prompt_text, document_files)
            log.info(f"Length of messages is - {len(messages[0])}")
            log.info(f"Message keys - {messages[0].keys()}")
            log.info(f"Role of messages is - {len(messages[0]['role'])}")
            
            for c in messages[0]["content"]:
                log.info(f"Message content keys - {c.keys()}")
                log.info(f"Message content type - {c['type']}")
            
            log.info(f"[{context}] Calling model '{settings.API_MODEL}' with {len(document_files)} page(s).")
            log.info(f"[{context}] Sending payload to LLM (first 500 chars): {str(messages)[:500]}")
            
            # Determine root_trace_id: explicit > request.state > new UUID
            if explicit_root_trace_id:
                root_trace_id = explicit_root_trace_id
            elif request and getattr(request.state, "root_trace_id", None):
                root_trace_id = request.state.root_trace_id
            else:
                root_trace_id = uuid.uuid4().hex
            
            span_id = uuid.uuid4().hex[:16]
            traceparent = f"00-{root_trace_id}-{span_id}-01"
            
            headers = {"traceparent": traceparent}  
            log.info(f"[{context}] Root Trace: {root_trace_id} | Span: {span_id} | Traceparent: {traceparent}")

            for attempt in range(settings.API_MAX_RETRIES):
                try:
                    start_time = time.perf_counter()
                    response = await asyncio.wait_for(
                        self._client.chat.completions.parse(
                            model=settings.API_MODEL, 
                            messages=messages, 
                            response_format=response_schema, 
                            temperature=0.0, 
                            reasoning_effort="disable",
                            extra_headers=headers,
                            max_completion_tokens=settings.MAX_COMPLETION_TOKENS,
                            max_tokens = settings.MAX_COMPLETION_TOKENS
                        ),
                        timeout=settings.LLM_CALL_TIMEOUT
                    )
                    end_time = time.perf_counter()
                    duration_ms = (end_time - start_time) * 1000

                    token_usage = {}
                    if response.usage:
                        token_usage = {
                            "completion_tokens": getattr(response.usage, 'completion_tokens', 0) or 0,
                            "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0) or 0,
                            "total_tokens": getattr(response.usage, 'total_tokens', 0) or 0,
                            "prompt_tokens_details": getattr(response.usage, "prompt_tokens_details", None) or {}
                        }
                    else:
                        token_usage = {
                            "completion_tokens": 0,
                            "prompt_tokens": 0,
                            "total_tokens": 0,
                            "prompt_tokens_details": {}
                        }
                    chat_id = response.id if response.id else "N/A"
                    log.info(
                        f"[{context}] LLM call successful. Neev Chat ID: {chat_id} "
                        f"Duration: {duration_ms:.2f}ms. "
                        f"Tokens: Prompt={token_usage.get('prompt_tokens')}, Completion={token_usage.get('completion_tokens')}, Total={token_usage.get('total_tokens')}."
                        )
                        # log.info(f"[{context}] LLM call successful but no token usage info returned.")

                    log.info(f"[{context}] Final token_usage to return: {token_usage}")
                    log.info(f"[{context}] Released semaphore. ")

                    # Return parsed response AND metrics
                    metrics = {
                        "latency_ms": duration_ms,
                        "token_usage": token_usage,
                    }
                    return response.choices[0].message.parsed, metrics
                
                except asyncio.TimeoutError:
                    log.error(f"[{context}] LLM HARD TIMEOUT after {settings.LLM_CALL_TIMEOUT}s.")
                    return {"error": f"LLM request exceeded {settings.LLM_CALL_TIMEOUT}s timeout", "raw_response": "TimeoutError"}, {}
                
                except ContentFilterFinishReasonError as e:
                    log.error(f"[{context}] Request rejected by content filter on attempt {attempt + 1}. This is a final error.")
                    log.info(f"[{context}] Releasing semaphore on content filter error. Slots available after release: {self._semaphore._value}")
                    return {"error": f"Unexpected Application Error (ContentFilterFinishReasonError): {e}", "raw_response": str(e)}, {}

                except ValidationError as e:
                    log.warning(f"[{context}] Pydantic validation failed on API attempt {attempt + 1}. Raw error: {e}")
                    raw_output = str(e.llm_output) if hasattr(e, 'llm_output') else str(e)
                    
                    recovered_json = self._extract_json_from_text(raw_output)
                    if recovered_json:
                        log.info(f"[{context}] Successfully recovered JSON from malformed response.")
                        try:
                            validated_model = response_schema.model_validate(recovered_json)
                            # LOG: Announce release before returning
                            log.info(f"[{context}] Releasing semaphore after successful JSON recovery.")
                            return validated_model, {}
                        except ValidationError as inner_e:
                            log.warning(f"[{context}] Recovered JSON still failed validation: {inner_e}")
                            raw_output = str(inner_e)
                    
                    # Note: The re-ask is part of the same "slot", so we don't release the semaphore here.
                    log.info(f"[{context}] Attempting targeted re-ask. Semaphore remains held.")
                    return await self._attempt_targeted_reask(prompt_text, document_files, response_schema, raw_output, context, root_trace_id , srn_no, fallback_json=recovered_json)
                
                except (RateLimitError, APIConnectionError, APIStatusError) as e:
                    wait_time = (settings.EXPONENTIAL_BACKOFF_FACTOR ** attempt) + random.uniform(0, 1)
                    log.warning(f"[{context}] API error '{e.__class__.__name__}' on attempt {attempt + 1}. Retrying in {wait_time:.2f}s.")
                    await asyncio.sleep(wait_time)
                    
                except Exception as e:
                    log.error(f"[{context}] An unexpected error occurred: error '{e.__class__.__name__}' Details: {e}", exc_info=True)
                    log.info(f"[{context}] Releasing semaphore on unexpected error.")
                    return {"error": f"Unexpected Application Error: error '{e.__class__.__name__}' Details: {e}", "raw_response": str(e)}, {}
            
            log.error(f"[{context}] API call failed after {settings.API_MAX_RETRIES} retries.")
            # LOG: Announce release before returning
            log.info(f"[{context}] Releasing semaphore after exhausted retries.")
            return {"error": "API Error after all retries", "raw_response": "Exhausted API retries"}, {}

    async def close(self):
        if self._client and not self._client.is_closed():
            await self._client.close()
            log.info("Closed LLMClient.")

llm_client = LLMClient()
