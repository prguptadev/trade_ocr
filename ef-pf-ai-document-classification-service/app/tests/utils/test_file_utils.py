import unittest
import json
from unittest.mock import patch, mock_open

# Assuming the file is located at app/utils/file_utils.py
from app.utils.file_utils import read_mapping_file, create_random_to_original_filename_lookup

class TestFileUtils(unittest.TestCase):

    def test_read_mapping_file_success(self):
        """
        Tests that a valid JSON file is read and parsed correctly.
        """
        mock_data = json.dumps([{"random_filename": "rand1.jpg", "original_filename": "orig1.jpg"}])
        # Use mock_open to simulate reading from a file without touching the disk
        with patch("builtins.open", mock_open(read_data=mock_data)) as mocked_file:
            result = read_mapping_file("any/path/file.json")
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["random_filename"], "rand1.jpg")
            mocked_file.assert_called_with("any/path/file.json", "r", encoding="utf-8")

    def test_read_mapping_file_not_found(self):
        """
        Tests that a FileNotFoundError is raised when the file doesn't exist.
        """
        # Configure the mocked open to raise FileNotFoundError
        with patch("builtins.open", mock_open()) as mocked_file:
            mocked_file.side_effect = FileNotFoundError
            with self.assertRaises(FileNotFoundError):
                read_mapping_file("nonexistent/file.json")

    def test_read_mapping_file_json_decode_error(self):
        """
        Tests that a JSONDecodeError is raised for an invalid JSON file.
        """
        invalid_json = "{'bad': json}"
        with patch("builtins.open", mock_open(read_data=invalid_json)):
            with self.assertRaises(json.JSONDecodeError):
                read_mapping_file("invalid.json")

    def test_create_random_to_original_filename_lookup(self):
        """
        Tests that the lookup dictionary is created correctly from mapping data.
        """
        mapping_data = [
            {"random_filename": "rand1.jpg", "original_filename": "orig1.jpg"},
            {"random_filename": "rand2.pdf", "original_filename": "orig2.pdf"},
            {"random_filename": "rand3.png", "original_filename": "orig3.png"},
            # Test a case with missing keys, which should be ignored
            {"some_other_key": "value"}
        ]
        
        lookup = create_random_to_original_filename_lookup(mapping_data)
        
        self.assertEqual(len(lookup), 3)
        self.assertEqual(lookup["rand1.jpg"], "orig1.jpg")
        self.assertEqual(lookup["rand2.pdf"], "orig2.pdf")
        self.assertNotIn("rand4.jpg", lookup)

if __name__ == '__main__':
    unittest.main()
