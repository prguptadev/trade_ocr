import unittest
from unittest.mock import patch, MagicMock

# Assuming the file is located at app/utils/file_system_utils.py
from app.utils.file_system_utils import FileSystemUtil

class TestFileSystemUtil(unittest.TestCase):

    def setUp(self):
        """Set up a new FileSystemUtil instance for each test."""
        self.fs_util = FileSystemUtil()

    @patch("os.path.isdir", return_value=True)
    @patch("os.listdir", return_value=["file1.txt", "file2.jpg"])
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.open", new_callable=unittest.mock.mock_open, read_data=b"test content")
    def test_list_local_files(self, mock_open, mock_isfile, mock_listdir, mock_isdir):
        """
        Tests listing files from a local directory.
        """
        folder_path = "/fake/local/dir"
        file_list = self.fs_util.list_files(folder_path)

        self.assertEqual(len(file_list), 2)
        self.assertEqual(file_list[0]["filename"], "file1.txt")
        self.assertEqual(file_list[1]["filename"], "file2.jpg")
        
        # Test the read_bytes function
        content = file_list[0]["read_bytes"]()
        self.assertEqual(content, b"test content")
        mock_isdir.assert_called_with(folder_path)
        mock_listdir.assert_called_with(folder_path)

    @patch("os.path.isdir", return_value=False)
    def test_list_local_files_not_found(self, mock_isdir):
        """
        Tests that FileNotFoundError is raised for a non-existent local directory.
        """
        with self.assertRaises(FileNotFoundError):
            self.fs_util.list_files("/non/existent/dir")

    @patch("google.cloud.storage.Client")
    def test_list_gcs_files(self, mock_storage):
        """
        Tests listing files from a GCS path.
        """
        gcs_path = "gs://my-bucket/my-folder/"

        # Mock the GCS client and blob objects
        mock_blob1 = MagicMock()
        mock_blob1.name = "my-folder/file1.txt"
        mock_blob1.download_as_bytes.return_value = b"gcs content 1"

        mock_blob2 = MagicMock()
        mock_blob2.name = "my-folder/file2.jpg"
        mock_blob2.download_as_bytes.return_value = b"gcs content 2"
        
        # Mock the GCS client and its methods
        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = [mock_blob1, mock_blob2]
        mock_gcs_client_instance = MagicMock()
        mock_gcs_client_instance.bucket.return_value = mock_bucket
        mock_storage.return_value = mock_gcs_client_instance

        file_list = self.fs_util.list_files(gcs_path)
        
        self.assertEqual(len(file_list), 2)
        self.assertEqual(file_list[0]["filename"], "file1.txt")
        
        # Test the read_bytes function from GCS
        content = file_list[1]["read_bytes"]()
        self.assertEqual(content, b"gcs content 2")
        
        # Verify that the GCS client was initialized and used
        mock_storage.assert_called_once()
        mock_gcs_client_instance.bucket.assert_called_with("my-bucket")
        mock_bucket.list_blobs.assert_called_with(prefix="my-folder/")

if __name__ == '__main__':
    unittest.main()
