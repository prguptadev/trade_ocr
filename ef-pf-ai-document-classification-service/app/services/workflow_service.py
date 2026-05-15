import os
import logging
from typing import Dict, List

from .ai_provider_interface import AIProviderInterface
from .document_processor import DocumentProcessor
from ..utils.file_utils import create_random_to_original_filename_lookup, read_mapping_file
from ..schemas import ClassifiedDocumentsResponse, ProcessFolderRequest
from .. import prompts
from ..config import settings
# from .classification_judge import ClassificationJudge 

logger = logging.getLogger(__name__)


class WorkflowService:
    def __init__(self, ai_provider: AIProviderInterface):
        self.ai_provider = ai_provider
        self.doc_processor = DocumentProcessor()
        # self.classification_judge = ClassificationJudge()  

        # Iterate over the list of folders.
        app_root = os.path.dirname(os.path.abspath(__file__))
        data_folder = os.path.join(os.path.dirname(app_root), "data")
        few_shot_folder_preprocessed = self.doc_processor.preprocess_folder(
            os.path.join(data_folder, settings.EXAMPLE_FOLDER)
        )

        fs_df_input_parts = []
        fs_df_input_parts.append({"type": "text", "text": f"Important: Example CRL Document Page Images: "})
        fs_df_input_parts.append({"type": "text", "text": f"["})
        for fs_df_doc in few_shot_folder_preprocessed:
            if "crl" in fs_df_doc["filename"]:
                fs_df_doc_filename = fs_df_doc["filename"]
                for img in fs_df_doc["images"]:
                    fs_df_input_parts.append({"type": "text", "text": f"{{"})
                    fs_df_input_parts.append(
                        {"type": "text", "text": f"\"document_page_image_filename\": \"{fs_df_doc_filename}\","}
                    )
                    fs_df_input_parts.append(
                        {"type": "text", "text": f"\"document_page_image_mime_type\": \"{img['mime_type']}\","}
                    )
                    fs_df_input_parts.append({"type": "text", "text": f"\"document_page_image_bytes\": \n\""})
                    fs_df_input_parts.append(
                        {"type": "image_url", "image_url": f"data:{img['mime_type']};base64,{img['data']}"}
                    )
                    fs_df_input_parts.append({"type": "text", "text": f"\"\n}},"})
        fs_df_input_parts.append({"type": "text", "text": f"]\n"})
        self.fs_df_input_parts = fs_df_input_parts
        logger.info("Processing folder - %s.", settings.EXAMPLE_FOLDER)

    def process_folder(self, request: ProcessFolderRequest, job_id: str) -> ClassifiedDocumentsResponse:
        log_extra = {"job_id": job_id, "folder_path": request.folder_path, "srn_no": request.srn_no}

        logger.info("Starting document preprocessing.", extra=log_extra)
        preprocessed_output = self.doc_processor.preprocess_folder(request.folder_path)

        if not preprocessed_output:
            logger.warning("No processable files found in the folder.", extra=log_extra)
            return ClassifiedDocumentsResponse(
                request=request,
                job_id=job_id,
                documents=[],
                processing_metadata={"notes": "No files were found to process."},
            )

        logger.info("Preprocessing complete. Found %d pages.", len(preprocessed_output), extra=log_extra)

        # Prompts
        prompt_to_use_classification = prompts.document_clustering_classification_si_prompt_multi_pages_9
        prompt_to_use_sequencing = prompts.document_sequencing_si_prompt_multi_pages_1

        # Prepare parts for the classification prompt
        df_input_parts = []
        image_bytes_dict: Dict[str, str] = {}
        image_mime_type_dict: Dict[str, str] = {}
        synthetic_to_source: Dict[str, str] = {}  # synthetic page id -> original filename

        df_input_parts.append({"type": "text", "text": f"["})
        for df_doc in preprocessed_output:
            df_doc_filename = df_doc["filename"]
            image = 0
            num_images = len(df_doc["images"])
            append_image_index_to_filename = True if num_images > 1 else False

            for img in df_doc["images"]:
                
                # They are not safe to pass to extraction services that expect real filenames.
                df_doc_image_filename = (
                    f"{df_doc_filename}_{image}" if append_image_index_to_filename else f"{df_doc_filename}"
                )

                synthetic_to_source[df_doc_image_filename] = df_doc_filename

                df_input_parts.append({"type": "text", "text": f"{{"})
                df_input_parts.append(
                    {"type": "text", "text": f"\"document_page_image_filename\": \"{df_doc_image_filename}\","}
                )
                df_input_parts.append(
                    {"type": "text", "text": f"\"document_page_image_mime_type\": \"{img['mime_type']}\","}
                )
                df_input_parts.append({"type": "text", "text": f"\"document_page_image_bytes\": \n\""})
                df_input_parts.append(
                    {"type": "image_url", "image_url": f"data:{img['mime_type']};base64,{img['data']}"}
                )
                df_input_parts.append({"type": "text", "text": f"\"\n}},"})

                image_bytes_dict[df_doc_image_filename] = img["data"]
                image_mime_type_dict[df_doc_image_filename] = img["mime_type"]
                image += 1

        df_input_parts.append({"type": "text", "text": f"]"})

        # Few-shot behavior: keep existing default ON; allow disabling if settings has USE_FEW_SHOT
        use_few_shot = getattr(settings, "USE_FEW_SHOT", True)
        few_shot_parts = self.fs_df_input_parts if use_few_shot else []
        if not use_few_shot:
            logger.info(
                "Few-shot examples disabled via settings.USE_FEW_SHOT; sending only folder images.",
                extra={**log_extra, "metric_type": "few_shot"},
            )

        logger.info("Invoking AI provider for clustering, and classification", extra=log_extra)

        ai_response_classification = self.ai_provider.cluster_classify_and_sequence(
            request=request,
            image_parts=df_input_parts + few_shot_parts,
            prompt=prompt_to_use_classification,
            job_id=job_id,
        )

        # Aggregate metrics from classification call
        classification_latency = ai_response_classification.processing_metadata.get("ai_call_latency_ms", 0)
        total_latency = classification_latency
        token_usage_1 = ai_response_classification.processing_metadata.get("token_usage", {})
        total_completion_tokens = token_usage_1.get("completion_tokens", 0)
        total_prompt_tokens = token_usage_1.get("prompt_tokens", 0)
        total_total_tokens = token_usage_1.get("total_tokens", 0)
        total_text_tokens = token_usage_1.get("prompt_tokens_details", {}).get("text_tokens", 0)

        # Build sequencing prompt context from classification output
        df_input_parts_1 = []
        df_input_parts_1.append({"type": "text", "text": f"{{"})

        unknown_pages_for_sequencing: List[str] = []

        for document in ai_response_classification.documents:
            df_input_parts_1.append({"type": "text", "text": f"\"{document.document_id}\": {{"})
            df_input_parts_1.append({"type": "text", "text": f"\"document_type\": \"{document.document_type}\","})
            df_input_parts_1.append({"type": "text", "text": f"\"pages\": ["})

            for page in document.pages:
                # SAFETY: model could emit a page id we don't have bytes for; skip it in sequencing input.
                if page not in image_mime_type_dict or page not in image_bytes_dict:
                    unknown_pages_for_sequencing.append(page)
                    continue

                page_mime_type = image_mime_type_dict[page]
                page_bytes = image_bytes_dict[page]

                df_input_parts_1.append({"type": "text", "text": f"{{"})
                df_input_parts_1.append({"type": "text", "text": f"\"page_image_filename\": \"{page}\","})
                df_input_parts_1.append({"type": "text", "text": f"\"page_image_mime_type\": \"{page_mime_type}\","})
                df_input_parts_1.append({"type": "text", "text": f"\"page_image_bytes\": \n\""})
                df_input_parts_1.append(
                    {"type": "image_url", "image_url": f"data:{page_mime_type};base64,{page_bytes}"}
                )
                df_input_parts_1.append({"type": "text", "text": f"\"\n}},"})

            df_input_parts_1.append({"type": "text", "text": f"]"})
            df_input_parts_1.append({"type": "text", "text": f"}},"})

        df_input_parts_1.append({"type": "text", "text": f"}}"})

        if unknown_pages_for_sequencing:
            logger.warning(
                "Classifier output contained pages not present in preprocessing map; sequencing input will skip them. count=%d",
                len(unknown_pages_for_sequencing),
                extra={**log_extra, "metric_type": "sequencing_input_integrity"},
            )

        logger.info("Invoking AI provider for sequencing", extra=log_extra)

        ai_response_sequence = self.ai_provider.cluster_classify_and_sequence(
            request=request,
            image_parts=df_input_parts_1 + few_shot_parts,
            prompt=prompt_to_use_sequencing,
            job_id=job_id,
        )

        # Aggregate sequencing call metrics
        sequencing_latency = ai_response_sequence.processing_metadata.get("ai_call_latency_ms", 0)
        total_latency += sequencing_latency
        token_usage_2 = ai_response_sequence.processing_metadata.get("token_usage", {})
        total_completion_tokens += token_usage_2.get("completion_tokens", 0)
        total_prompt_tokens += token_usage_2.get("prompt_tokens", 0)
        total_total_tokens += token_usage_2.get("total_tokens", 0)
        total_text_tokens += token_usage_2.get("prompt_tokens_details", {}).get("text_tokens", 0)

        aggregated_token_usage = {
            "completion_tokens": total_completion_tokens,
            "prompt_tokens": total_prompt_tokens,
            "total_tokens": total_total_tokens,
            "prompt_tokens_details": {"text_tokens": total_text_tokens},
        }

        # Keep old fields, add per-phase blocks (
        aggregated_processing_metadata = {
            "ai_call_latency_ms": total_latency,
            "token_usage": aggregated_token_usage,
            "classification": {
                "ai_call_latency_ms": classification_latency,
                "token_usage": token_usage_1,
            },
            "sequencing": {
                "ai_call_latency_ms": sequencing_latency,
                "token_usage": token_usage_2,
            },
        }

        if unknown_pages_for_sequencing:
            aggregated_processing_metadata["unknown_pages_for_sequencing_input"] = unknown_pages_for_sequencing[:200]

        # Sequencing safety: ensure sequencing didn't drop/alter the page set 
        def _collect_pages(ai_resp) -> set:
            pages = set()
            for d in getattr(ai_resp, "documents", []) or []:
                for p in getattr(d, "pages", []) or []:
                    pages.add(p)
            return pages

        cls_pages = _collect_pages(ai_response_classification)
        seq_pages = _collect_pages(ai_response_sequence)

        final_ai_response = ai_response_sequence
        if cls_pages != seq_pages:
            missing_in_seq = sorted(list(cls_pages - seq_pages))
            extra_in_seq = sorted(list(seq_pages - cls_pages))

            logger.warning(
                "Sequencing dropped/changed pages. Falling back to classification output. "
                "cls_pages=%d seq_pages=%d missing_in_seq=%d extra_in_seq=%d",
                len(cls_pages),
                len(seq_pages),
                len(missing_in_seq),
                len(extra_in_seq),
                extra=log_extra,
            )

            aggregated_processing_metadata["sequencing_fallback"] = {
                "enabled": True,
                "reason": "page_set_mismatch_between_classification_and_sequencing",
                "classification_page_count": len(cls_pages),
                "sequencing_page_count": len(seq_pages),
                "missing_in_sequencing": missing_in_seq[:200],
                "extra_in_sequencing": extra_in_seq[:200],
            }
            final_ai_response = ai_response_classification
        else:
            aggregated_processing_metadata["sequencing_fallback"] = {
                "enabled": False,
                "classification_page_count": len(cls_pages),
                "sequencing_page_count": len(seq_pages),
            }
            logger.info(
                "Sequencing page consistency check passed. Total pages preserved: %d",
                len(cls_pages),
                extra={**log_extra, "metric_type": "sequencing_consistency"},
            )

        logger.info("AI provider returned %d documents.", len(final_ai_response.documents), extra=log_extra)

       
        # Example: ["CRL.pdf_0","CRL1.pdf","CRL.pdf_1"] -> ["CRL.pdf","CRL1.pdf"]
        def _dedupe_preserve_order(items: List[str]) -> List[str]:
            seen = set()
            out = []
            for x in items:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            return out

        def _collapse_pages_for_extraction(doc_pages: List[str]) -> List[str]:
            real_pages = []
            for p in doc_pages or []:
                real_pages.append(synthetic_to_source.get(p, p))
            return _dedupe_preserve_order(real_pages)

        any_synthetic_pages = False
        page_map: Dict[str, List[str]] = {}
        unknown_pages: List[str] = []

        for doc in final_ai_response.documents:
            for p in getattr(doc, "pages", []) or []:
                real = synthetic_to_source.get(p)
                if real is None:
                    unknown_pages.append(p)
                    continue
                if real != p:
                    any_synthetic_pages = True
                    page_map.setdefault(real, [])
                    page_map[real].append(p)

        if any_synthetic_pages:
            aggregated_processing_metadata["page_map"] = page_map
            logger.info(
                "Rewriting synthetic page ids to extractor-safe filenames.",
                extra={**log_extra, "metric_type": "extraction_compat_pages"},
            )

        if unknown_pages:
            aggregated_processing_metadata["unknown_pages_not_in_preprocess_map"] = unknown_pages[:200]
            logger.warning(
                "Some pages in model output were not present in preprocessing map; leaving them unchanged.",
                extra={**log_extra, "metric_type": "extraction_compat_pages"},
            )

        for doc in final_ai_response.documents:
            doc.pages = _collapse_pages_for_extraction(doc.pages)

        return ClassifiedDocumentsResponse(
            request=request,
            job_id=job_id,
            documents=final_ai_response.documents,
            processing_metadata=aggregated_processing_metadata,
        )
