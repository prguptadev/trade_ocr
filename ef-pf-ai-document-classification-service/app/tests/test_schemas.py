import unittest
from pydantic import ValidationError

# Assuming schemas.py is in the same directory.
from app.schemas import (
    ProcessFolderRequest,
    ClassifiedDocument,
    ClassifiedDocumentsResponse,
    DocumentPageSummary,
    DocumentAnalysis,
    KeyField
)

class TestSchemas(unittest.TestCase):

    def test_process_folder_request_success(self):
        """Tests successful creation of ProcessFolderRequest."""
        data = {"folder_path": "/path/to/docs", "srn_no": "SRN12345"}
        req = ProcessFolderRequest(**data)
        self.assertEqual(req.folder_path, data["folder_path"])
        self.assertEqual(req.srn_no, data["srn_no"])

    def test_process_folder_request_fail(self):
        """Tests that ProcessFolderRequest fails without required fields."""
        with self.assertRaises(ValidationError):
            ProcessFolderRequest(folder_path="/path/to/docs")

    def test_classified_document_success(self):
        """Tests successful creation of ClassifiedDocument."""
        data = {
            "document_id": "doc-1",
            "document_type": "Invoice",
            "pages": ["page1.png", "page2.png"],
            "confidence_score": 98.5
        }
        doc = ClassifiedDocument(**data)
        self.assertEqual(doc.confidence_score, 98.5)
        self.assertEqual(len(doc.pages), 2)

    def test_classified_document_invalid_confidence(self):
        """Tests that confidence_score outside the valid range raises an error."""
        invalid_data = {
            "document_type": "Invoice",
            "pages": ["page1.png"],
            "confidence_score": 101.0  # Invalid score
        }
        with self.assertRaises(ValidationError):
            ClassifiedDocument(**invalid_data)

    def test_classified_documents_response_nested_success(self):
        """Tests successful creation of the full nested response."""
        data = {
            "request": {"folder_path": "/path/to/docs", "srn_no": "SRN12345"},
            "job_id": "job-xyz-789",
            "documents": [
                {
                    "document_id": "doc-1",
                    "document_type": "Invoice",
                    "pages": ["page1.png"],
                    "confidence_score": 95.0
                }
            ],
            "processing_metadata": {"time_taken": "1.2s"}
        }
        response = ClassifiedDocumentsResponse(**data)
        self.assertEqual(response.job_id, "job-xyz-789")
        self.assertEqual(response.request.srn_no, "SRN12345")
        self.assertEqual(len(response.documents), 1)
        self.assertEqual(response.documents[0].document_type, "Invoice")

    def test_document_page_summary_complex_nesting(self):
        """
        Tests a more complex nested schema, DocumentPageSummary, to ensure
        deep structures are validated correctly.
        """
        data = {
            "filename": "page_01.png",
            "document_analysis": {
                "document_types_guess": ["report", "summary"],
                "overall_summary": "This page contains financial data.",
                "language": "en"
            },
            "key_fields": [
                {"field_name": "Total Amount", "field_value": "$5,000"}
            ]
        }
        summary = DocumentPageSummary(**data)
        self.assertEqual(summary.filename, "page_01.png")
        self.assertIsInstance(summary.document_analysis, DocumentAnalysis)
        self.assertEqual(summary.document_analysis.language, "en")
        self.assertEqual(len(summary.key_fields), 1)
        self.assertIsInstance(summary.key_fields[0], KeyField)
        self.assertEqual(summary.key_fields[0].field_name, "Total Amount")

if __name__ == '__main__':
    unittest.main()
