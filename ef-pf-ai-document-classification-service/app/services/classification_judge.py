import logging
from typing import Any, Dict, List

from .classification_judge_engine import ClassificationJudgeEngine

logger = logging.getLogger(__name__)


class ClassificationJudge:
    """
    High-level judge used by WorkflowService.

    - Wraps ClassificationJudgeEngine.
    - Provides a simple .judge_classification(...) API.
    """

    def __init__(self) -> None:
        self._engine = ClassificationJudgeEngine()

    def judge_classification(
        self,
        classification: Dict[str, Any],
        batch_label: str,
        image_folder: str,
    ) -> Dict[str, Any]:
        """
        classification: {
            "documents": [
                {
                    "document_id": ...,
                    "document_type": ...,
                    "pages": [...],
                    "confidence_score": <numeric>,
                },
                ...
            ],
            ...
        }
        """
        self._engine.reset_metrics()
        logger.info("Using image folder for judge: %s", image_folder)

        image_index = self._engine.build_image_index(image_folder)

        docs = classification.get("documents", [])
        results: List[Dict[str, Any]] = []

        for doc in docs:
            try:
                judged = self._engine.judge_single_document(
                    doc_record=doc,
                    image_index=image_index,
                    batch_label=batch_label,
                )
                results.append(judged)
            except Exception as e:
                logger.error(
                    "Failed to judge document '%s': %s",
                    doc.get("document_id"),
                    e,
                    exc_info=True,
                )

        num_docs = len(results)
        avg_conf = (
            sum(d["final_confidence"] for d in results) / num_docs if num_docs > 0 else 0.0
        )

        summary = {
            "num_documents": num_docs,
            "average_final_confidence": avg_conf,
            "judge_latency_ms": round(self._engine.current_judge_latency_ms, 2),
            "judge_token_usage": {
                "prompt_tokens": int(
                    self._engine.current_judge_token_usage["prompt_tokens"]
                ),
                "completion_tokens": int(
                    self._engine.current_judge_token_usage["completion_tokens"]
                ),
                "total_tokens": int(
                    self._engine.current_judge_token_usage["total_tokens"]
                ),
            },
        }

        return {
            "documents": results,
            "summary": summary,
        }
