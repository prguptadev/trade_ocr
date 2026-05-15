import unittest
# Use absolute imports based on your project structure
from app.prompts import (
    document_image_clustering_domain_context,
    document_understanding_and_extraction_si_prompt_single_page,
    document_understanding_and_extraction_si_prompt_multi_pages,
    document_clustering_sequencing_classification_si_prompt_multi_pages_1,
    document_clustering_classification_si_prompt_multi_pages_1,
    document_clustering_classification_si_prompt_multi_pages_2,
    document_clustering_classification_si_prompt_multi_pages_3,
    document_clustering_classification_si_prompt_multi_pages_9,
    document_clustering_sequencing_classification_si_prompt_multi_pages_5,
    document_clustering_sequencing_classification_si_prompt_multi_pages_9,
    document_clustering_sequencing_classification_si_prompt_multi_pages_8,
)

class TestPrompts(unittest.TestCase):

    def test_prompts_are_non_empty_strings(self):
        """
        Tests that all imported prompt variables are non-empty strings.
        """
        prompts = {
            "document_image_clustering_domain_context": document_image_clustering_domain_context,
            "document_understanding_and_extraction_si_prompt_single_page": document_understanding_and_extraction_si_prompt_single_page,
            "document_understanding_and_extraction_si_prompt_multi_pages": document_understanding_and_extraction_si_prompt_multi_pages,
            "document_clustering_sequencing_classification_si_prompt_multi_pages_1": document_clustering_sequencing_classification_si_prompt_multi_pages_1,
            "document_clustering_classification_si_prompt_multi_pages_1": document_clustering_classification_si_prompt_multi_pages_1,
            "document_clustering_classification_si_prompt_multi_pages_2": document_clustering_classification_si_prompt_multi_pages_2,
            "document_clustering_classification_si_prompt_multi_pages_3": document_clustering_classification_si_prompt_multi_pages_3,
            "document_clustering_classification_si_prompt_multi_pages_9": document_clustering_classification_si_prompt_multi_pages_9,
            "document_clustering_sequencing_classification_si_prompt_multi_pages_5": document_clustering_sequencing_classification_si_prompt_multi_pages_5,
            "document_clustering_sequencing_classification_si_prompt_multi_pages_9": document_clustering_sequencing_classification_si_prompt_multi_pages_9,
            "document_clustering_sequencing_classification_si_prompt_multi_pages_8": document_clustering_sequencing_classification_si_prompt_multi_pages_8,
        }

        for name, prompt in prompts.items():
            with self.subTest(prompt_name=name):
                self.assertIsInstance(prompt, str, f"Prompt '{name}' should be a string.")
                self.assertTrue(prompt.strip(), f"Prompt '{name}' should not be empty.")

if __name__ == '__main__':
    unittest.main()
