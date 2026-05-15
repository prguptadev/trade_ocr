import httpx
import openai
import json
import time
import asyncio
import random
import logging
from typing import List, Dict, Any, Optional
from .ai_provider_interface import AIProviderInterface
#from .llm_client import LLMOpenAIClient
from ..config import settings
from ..schemas import ClassifiedDocumentsResponse, NonExtractedDocumentsWithConfidenceLevel, ProcessFolderRequest

logger = logging.getLogger(__name__)

class OpenAIProvider(AIProviderInterface):
    """Concrete implementation of the AI provider for OpenAI-compatible APIs."""

    def __init__(
        self,
        max_retries: int = 3,
        json_correction_attempts: int = 2,
        backoff_factor: int = 2,
        http_client: Optional[httpx.Client] = None,
    ):
        # Allow sharing an httpx.Client, but preserve existing default behavior
        if http_client is None:
            http_client = httpx.Client(http2=True, verify=False)

        try:
            self.client = openai.OpenAI(
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
                http_client=http_client
            )
            
            # Store parameters for retry logic
            self.max_retries = max_retries
            self.json_correction_attempts = json_correction_attempts
            self.backoff_factor = backoff_factor
            
            logger.info("OpenAI client initialized successfully.")
        except Exception as e:
            logger.error("Failed to initialize OpenAI client", exc_info=True)
            raise

    
    def cluster_classify_and_sequence(
        self,
        request: ProcessFolderRequest,
        image_parts: List[Dict],
        prompt: str,
        job_id: str
    ) -> ClassifiedDocumentsResponse:
        """Synchronous method with retry logic and detailed metrics."""
        
        log_extra = {"job_id": job_id}
        
        # Prepare messages using original logic
        prompt_part = [{"type": "text", "text": prompt}]
        combined_parts = prompt_part + image_parts
        messages = [{"role": "user", "content": combined_parts}]
        
        start_time = time.perf_counter()
        
        # Use retry logic with all original settings
        for attempt in range(self.max_retries):
            try:
                logger.info(f"Length of messages is - {len(messages[0])}")
                logger.info(f"Message keys - {messages[0].keys()}")
                logger.info(f"Role of messages is - {len(messages[0]['role'])}")
                
                for c in messages[0]["content"]:
                    logger.info(f"Message content keys - {c.keys()}")
                    logger.info(f"Message content type - {c['type']}") 
                    
                response = self.client.chat.completions.parse(
                    model=settings.MODEL_NAME,
                    messages=messages,
                    temperature=settings.TEMPERATURE,
                    top_p=settings.TOP_P,
                    response_format=NonExtractedDocumentsWithConfidenceLevel,
                    reasoning_effort=settings.REASONING_EFFORT,
                    max_completion_tokens=settings.MAX_COMPLETION_TOKENS
                )
                logger.info(f"LLM response: {response.choices[0].message.content}")
                
                # Get token usage for logging
                token_usage = response.usage.to_dict() if response.usage else {}
                
                # Parse response with fallback logic
                try:
                    # Try parsed response first
                    if hasattr(response.choices[0].message, 'parsed') and response.choices[0].message.parsed:
                        parsed_response = response.choices[0].message.parsed
                    else:
                        # Fallback to manual parsing
                        response_json_str = response.choices[0].message.content.strip()
                        if response_json_str.startswith("```json"):
                            response_json_str = response_json_str[7:]
                        response_json_str = response_json_str.strip().removesuffix("```").strip()
                        raw_response = json.loads(response_json_str)
                        parsed_response = NonExtractedDocumentsWithConfidenceLevel.model_validate(raw_response)
                    
                    break  # Success - exit retry loop
                    
                except (IndexError, json.JSONDecodeError, Exception) as parse_e:
                    logger.error("Failed to parse or validate model response: %s. Response: '%s'", parse_e, getattr(response.choices[0].message, 'content', 'N/A'), extra=log_extra)
                    raise ValueError("Could not parse a valid JSON object from the model's response.")
                
            except Exception as e:
                last_exception = e
                err_name = e.__class__.__name__
                
                # Apply retry logic for specific errors
                if err_name in ['RateLimitError', 'APIConnectionError', 'APIStatusError']:
                    wait_time = (self.backoff_factor ** attempt) + random.uniform(0, 1)
                    logger.warning("[%s] API error '%s' on attempt %d. Retrying in %.2fs.", job_id, err_name, attempt + 1, wait_time)
                    time.sleep(wait_time)  # Use time.sleep for synchronous version
                    continue
                    
                elif err_name == 'ContentFilterFinishReasonError':
                    logger.error("[%s] Request rejected by content filter.", job_id)
                    raise ValueError("Request rejected by content filter.")
                    
                elif err_name == 'ValidationError':
                    # Try JSON extraction (simplified for synchronous version)
                    raw_output = str(e)
                    # Try to extract JSON using regex
                    import re
                    match = re.search(r'\{.*\}', raw_output, re.DOTALL)
                    if match:
                        try:
                            recovered_json = json.loads(match.group(0))
                            parsed_response = NonExtractedDocumentsWithConfidenceLevel.model_validate(recovered_json)
                            token_usage = {}
                            break
                        except Exception:
                            pass
                    
                    logger.error("[%s] Validation error could not be recovered: %s", job_id, e)
                    raise
                    
                else:
                    logger.error("API call to OpenAI provider failed", extra=log_extra, exc_info=True)
                    raise e
        else:
            # All retries exhausted
            logger.error("[%s] API call failed after %d retries.", job_id, self.max_retries)
            raise Exception("API Error after all retries") from last_exception

        end_time = time.perf_counter()
        latency_ms = (end_time - start_time) * 1000
        latency_ms_rounded = round(latency_ms, 2)

        # Original logging with token usage
        log_message = (
            f"AI call completed. Latency: {latency_ms_rounded}ms, "
            f"Tokens: {token_usage.get('total_tokens', 'N/A')} "
            f"(Prompt: {token_usage.get('prompt_tokens', 'N/A')}, "
            f"Completion: {token_usage.get('completion_tokens', 'N/A')})"
        )

        # --- Structured Metric Logging ---
        # This 'extra' data will be captured by the JSON logger for structured logs
        log_metric_data = {
            "job_id": job_id,
            "metric_type": "ai_call_performance",
            "model_name": settings.MODEL_NAME,
            "latency_ms": latency_ms_rounded,
            "token_usage": token_usage
        }

        logger.info(log_message, extra=log_metric_data)

        # Transform response to the standardized ClassifiedDocumentsResponse
        classified_docs = []
        for doc in parsed_response.documents:
            confidence_float = settings.CONFIDENCE_SCORE_MAPPING.get(doc.confidence_level.strip(), settings.DEFAULT_CONFIDENCE)
            classified_docs.append({
                "document_id": doc.document_id,
                "document_type": doc.document_type,
                # "document_summary": doc.document_summary,
                "pages": doc.pages,
                # "reasoning": None, # Set to None as the prompt doesn't request it
                "confidence_score": confidence_float # Set to None as the prompt doesn't request it
            })

        return ClassifiedDocumentsResponse(
            request=request,
            job_id=job_id,
            documents=classified_docs,
            processing_metadata={"ai_call_latency_ms": latency_ms, "token_usage": token_usage}
        )
    def chat_completion_judge(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ):
        """
        Thin wrapper around the OpenAI chat.completions.create API.

        This centralizes the actual LLM call so that other components
        (e.g. ClassificationJudgeEngine) do not talk to OpenAI directly.
        """
        if model is None:
            model = settings.MODEL_NAME
        if temperature is None:
            temperature = settings.TEMPERATURE

        return self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            **kwargs,
        )
