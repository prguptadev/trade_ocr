import os
import json
import time
import logging
from typing import Any, Dict, List, Optional

import httpx
import openai

from ..config import settings
from .document_processor import DocumentProcessor
from .openai_provider import OpenAIProvider
from ..prompts import JUDGE_CLUSTER_PROMPT_TEMPLATE
logger = logging.getLogger(__name__)

# CONFIG 

JUDGE_MODEL_NAME = getattr(settings, "JUDGE_MODEL_NAME", settings.MODEL_NAME)

# Weights for final confidence computation 
TYPE_WEIGHT = 0.25
CLUSTER_WEIGHT = 0.25
ENTITY_WEIGHT = 0.25
PAGE_ORDER_WEIGHT = 0.20
LLM_OVERALL_WEIGHT = 0.05

JUDGE_WEIGHT = 0.70
CLASSIFIER_WEIGHT = 0.30

TYPE_MISMATCH_PENALTY = 0.30
MISSING_PAGES_PENALTY = 0.50

# JSON UTILITIES

def _extract_json_from_text(text: str) -> Dict[str, Any]:
    """
    Extract and parse a JSON object from a model's text response.
    Handles ```json ... ``` fences and extra whitespace.
    """
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"Could not find JSON object in text: {text[:200]}...")
        snippet = match.group(0)
        return json.loads(snippet)


def _coerce_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except Exception:
        return default


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# FINAL CONFIDENCE


def compute_final_confidence(
    judge_result: Dict[str, Any],
    classifier_confidence: Optional[float],
) -> float:
    """
    Combine judge sub-scores and classifier confidence into a final [0,1] score.

    Heuristic:
      base = weighted combination of:
        - type_consistency_score (TYPE_WEIGHT)
        - cluster_coherence_score (CLUSTER_WEIGHT)
        - entity_consistency_score (ENTITY_WEIGHT)
        - page_order_score (PAGE_ORDER_WEIGHT)
        - llm_overall_confidence (LLM_OVERALL_WEIGHT)

      classifier_conf_norm = classifier_confidence / 100 (if 0-100) or x if already in [0,1],
                             default to 0.7 if missing.

      raw = JUDGE_WEIGHT * base + CLASSIFIER_WEIGHT * classifier_conf_norm

      Then apply penalties:
        - If type_match is false: multiply by TYPE_MISMATCH_PENALTY
        - If missing_or_extra_pages != "none": multiply by MISSING_PAGES_PENALTY
    """
    tc = _clamp01(_coerce_float(judge_result.get("type_consistency_score", 0.0)))
    cc = _clamp01(_coerce_float(judge_result.get("cluster_coherence_score", 0.0)))
    ec = _clamp01(_coerce_float(judge_result.get("entity_consistency_score", 0.0)))
    po = _clamp01(_coerce_float(judge_result.get("page_order_score", 0.0)))
    lo = _clamp01(_coerce_float(judge_result.get("llm_overall_confidence", 0.0)))

    base = (
        TYPE_WEIGHT * tc
        + CLUSTER_WEIGHT * cc
        + ENTITY_WEIGHT * ec
        + PAGE_ORDER_WEIGHT * po
        + LLM_OVERALL_WEIGHT * lo
    )

    if classifier_confidence is None:
        classifier_conf_norm = 0.7
    else:
        if classifier_confidence > 1.5:
            classifier_conf_norm = _clamp01(classifier_confidence / 100.0)
        else:
            classifier_conf_norm = _clamp01(classifier_confidence)

    raw = JUDGE_WEIGHT * base + CLASSIFIER_WEIGHT * classifier_conf_norm

    type_match = bool(judge_result.get("type_match", False))
    missing_flag = str(judge_result.get("missing_or_extra_pages", "none")).strip().lower()

    final = raw

    if not type_match:
        final *= TYPE_MISMATCH_PENALTY

    if missing_flag != "none":
        final *= MISSING_PAGES_PENALTY

    return _clamp01(final)


# ENGINE CLASS

class ClassificationJudgeEngine:
    """
    Core judge engine:
      - Uses DocumentProcessor to build image index.
      - Calls OpenAI sync client with text+image prompt.
      - Computes per-document final_confidence.
      - Tracks latency and token usage.
    """

    def __init__(self) -> None:
        http_client = httpx.Client(http2=True, verify=False)
        # Use OpenAIProvider as the single owner of the OpenAI client
        self.ai_provider = OpenAIProvider(http_client=http_client)
        # Preserve existing attribute in case external code relies on it
        self.client = self.ai_provider.client

        self.doc_processor = DocumentProcessor()
        self.current_judge_latency_ms: float = 0.0
        self.current_judge_token_usage: Dict[str, float] = {
            "prompt_tokens": 0.0,
            "completion_tokens": 0.0,
            "total_tokens": 0.0,
        }

    def reset_metrics(self) -> None:
        self.current_judge_latency_ms = 0.0
        self.current_judge_token_usage = {
            "prompt_tokens": 0.0,
            "completion_tokens": 0.0,
            "total_tokens": 0.0,
        }

    def build_image_index(self, folder_path: str) -> Dict[str, str]:
        """
        Use DocumentProcessor.preprocess_folder to build a mapping:
            page_id (e.g. "CRL1.pdf", "CRL1.pdf_0") -> data_url (data:image/...;base64,...)
        """
        logger.info("Preprocessing folder for judge: %s", folder_path)
        docs = self.doc_processor.preprocess_folder(folder_path)
        index: Dict[str, str] = {}

        for doc in docs:
            filename = doc["filename"]
            name_no_ext, ext = os.path.splitext(filename)
            images = doc.get("images", [])

            for img in images:
                page_num = img.get("page_number", 1)
                page_idx0 = page_num - 1
                mime_type = img.get("mime_type", "image/png")
                b64_data = img["data"]
                data_url = f"data:{mime_type};base64,{b64_data}"

                keys = {
                    filename,  # "CRL1.pdf"
                    f"{filename}_{page_idx0}",  # "CRL1.pdf_0"
                    f"{name_no_ext}_{page_idx0}{ext}",  # "CRL1_0.pdf"
                    f"{name_no_ext}{ext}_{page_idx0}",  # "CRL1.pdf_0" alt form
                }

                for k in keys:
                    if k not in index:
                        index[k] = data_url

        logger.info("Built image index with %d keys.", len(index))
        return index

    def _call_llm_with_images(
        self,
        prompt: str,
        image_parts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Call the OpenAI-compatible chat.completions endpoint with text + image parts.
        Returns parsed JSON from the model.
        """
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    *image_parts,
                ],
            }
        ]

        start = time.perf_counter()
        # Delegate the actual LLM call to OpenAIProvider
        response = self.ai_provider.chat_completion_judge(
            messages=messages,
            model=JUDGE_MODEL_NAME,
            temperature=0.0,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self.current_judge_latency_ms += elapsed_ms

        usage = getattr(response, "usage", None)
        if usage is not None:
            try:
                usage_dict = usage.to_dict()
            except Exception:
                usage_dict = dict(usage) if isinstance(usage, dict) else {}

            pt = usage_dict.get("prompt_tokens", 0) or 0
            ct = usage_dict.get("completion_tokens", 0) or 0
            tt = usage_dict.get("total_tokens", 0) or (pt + ct)

            self.current_judge_token_usage["prompt_tokens"] += pt
            self.current_judge_token_usage["completion_tokens"] += ct
            self.current_judge_token_usage["total_tokens"] += tt

        choice = response.choices[0]
        content = choice.message.content

        if isinstance(content, str):
            text = content
        else:
            parts_text: List[str] = []
            for c in content:
                t = getattr(c, "text", None)
                if t:
                    parts_text.append(t)
            text = "".join(parts_text)

        parsed = _extract_json_from_text(text)
        return parsed

    def judge_single_document(
        self,
        doc_record: Dict[str, Any],
        image_index: Dict[str, str],
        batch_label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Judge a single classified document.

        doc_record example:
            {
              "document_id": "doc_1",
              "document_type": "CRL",
              "pages": ["CRL1.pdf", "CRL2.pdf"],
              "confidence_score": 99
            }
        """
        doc_id = doc_record.get("document_id")
        claimed_type = doc_record.get("document_type")
        pages = doc_record.get("pages", [])
        classifier_conf = doc_record.get("confidence_score")

        logger.info("Judging document '%s' of type '%s'", doc_id, claimed_type)

        image_parts: List[Dict[str, Any]] = []
        for page_id in pages:
            page_id_str = str(page_id)
            url = image_index.get(page_id_str)
            if not url:
                name_no_ext, ext = os.path.splitext(page_id_str)
                alt1 = f"{page_id_str}_0"
                alt2 = f"{name_no_ext}_0{ext}"
                url = image_index.get(alt1) or image_index.get(alt2)

            if not url:
                logger.warning(
                    "No image found for page_id '%s'. It will be mentioned in the prompt, "
                    "but no image will be attached.",
                    page_id_str,
                )
                image_parts.append(
                    {"type": "text", "text": f"[MISSING_IMAGE_FOR_PAGE: {page_id_str}]"}
                )
                continue

            image_parts.append({"type": "text", "text": f"PAGE: {page_id_str}"})
            image_parts.append({"type": "image_url", "image_url": {"url": url}})

        classifier_snippet = json.dumps(
            {
                "document_id": doc_id,
                "document_type": claimed_type,
                "pages": pages,
                "classifier_confidence_score": classifier_conf,
                "batch_label": batch_label,
            },
            indent=2,
        )

        prompt = (
            JUDGE_CLUSTER_PROMPT_TEMPLATE
            + "\n\n-----------------------------\n"
            + "CLASSIFIER OUTPUT FOR THIS DOCUMENT:\n"
            + classifier_snippet
            + "\n\nNow produce your JSON response."
        )

        judge_raw = self._call_llm_with_images(prompt, image_parts)

        # Fallback in case the model ignores instructions and nests under "scores"
        scores_obj = judge_raw.get("scores", {})
        if isinstance(scores_obj, dict):
            judge_raw.setdefault(
                "type_consistency_score",
                scores_obj.get("type_consistency", scores_obj.get("type", 0.0)),
            )
            judge_raw.setdefault(
                "cluster_coherence_score", scores_obj.get("cluster_coherence", 0.0)
            )
            judge_raw.setdefault(
                "page_order_score", scores_obj.get("page_order", 0.0)
            )
            judge_raw.setdefault(
                "entity_consistency_score",
                scores_obj.get("entity_consistency", 0.0),
            )
            judge_raw.setdefault(
                "llm_overall_confidence", scores_obj.get("llm_overall", 0.0)
            )
            judge_raw.setdefault(
                "classifier_confidence", scores_obj.get("classifier_confidence", None)
            )

        judge_raw.setdefault("document_id", doc_id)
        judge_raw.setdefault("claimed_document_type", claimed_type)
        judge_raw.setdefault(
            "judged_document_type",
            claimed_type if classifier_conf and classifier_conf >= 90 else "UNKNOWN",
        )
        judge_raw.setdefault(
            "type_match",
            judge_raw["judged_document_type"] == judge_raw["claimed_document_type"],
        )
        judge_raw.setdefault("type_consistency_score", 0.0)
        judge_raw.setdefault("cluster_coherence_score", 0.0)
        judge_raw.setdefault("page_order_score", 0.0)
        judge_raw.setdefault("entity_consistency_score", 0.0)
        judge_raw.setdefault("llm_overall_confidence", 0.0)
        judge_raw.setdefault("classifier_confidence", None)
        judge_raw.setdefault("missing_or_extra_pages", "none")
        judge_raw.setdefault("issues", [])

        final_conf = compute_final_confidence(judge_raw, classifier_conf)

        # Normalize classifier_conf for output "scores"
        if classifier_conf is not None:
            if classifier_conf > 1.5:
                cls_norm = _clamp01(classifier_conf / 100.0)
            else:
                cls_norm = _clamp01(classifier_conf)
        else:
            cls_norm = None

        result = {
            "document_id": judge_raw["document_id"],
            "claimed_document_type": judge_raw["claimed_document_type"],
            "judged_document_type": judge_raw["judged_document_type"],
            "type_match": bool(judge_raw.get("type_match", False)),
            "scores": {
                "type_consistency": _clamp01(
                    _coerce_float(judge_raw["type_consistency_score"])
                ),
                "cluster_coherence": _clamp01(
                    _coerce_float(judge_raw["cluster_coherence_score"])
                ),
                "page_order": _clamp01(
                    _coerce_float(judge_raw["page_order_score"])
                ),
                "entity_consistency": _clamp01(
                    _coerce_float(judge_raw["entity_consistency_score"])
                ),
                "llm_overall": _clamp01(
                    _coerce_float(judge_raw["llm_overall_confidence"])
                ),
                "classifier_confidence": cls_norm,
            },
            "missing_or_extra_pages": judge_raw["missing_or_extra_pages"],
            "issues": judge_raw.get("issues", []),
            "final_confidence": final_conf,
        }

        return result
