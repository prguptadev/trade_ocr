from abc import ABC, abstractmethod
from typing import List, Dict
from ..schemas import ClassifiedDocumentsResponse, ProcessFolderRequest

class AIProviderInterface(ABC):
    """
    Abstract base class defining the contract for an AI provider.
    This allows for interchangeable AI backends (e.g., OpenAI, Vertex AI).
    """
    @abstractmethod
    def cluster_classify_and_sequence(
        self,
        request: ProcessFolderRequest,
        image_parts: List[Dict],
        prompt: str,
        job_id: str
    ) -> ClassifiedDocumentsResponse:
        """
        Processes a list of images to cluster, classify, and sequence them.
        """
        pass
