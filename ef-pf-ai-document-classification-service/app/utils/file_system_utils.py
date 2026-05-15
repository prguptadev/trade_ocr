import os
from typing import List, Dict, Tuple, Any
import logging

logger = logging.getLogger(__name__)

class FileSystemUtil:
    """
    A utility to abstract file system operations for both local and GCS paths.
    """
    def __init__(self):
        # Only initialize gcs_client when needed
        self.gcs_client = None

    def list_files(self, folder_path: str) -> List[Dict[str, Any]]:
        """
        Lists files from either a local directory or a GCS bucket.

        Returns a list of dictionaries, where each contains the filename
        and a function to read its content.
        """
        if folder_path.startswith('gs://'):
            logger.info(f"Detected Google Cloud Storage path: {folder_path}")
            # Lazy initialization
            if self.gcs_client is None:
                from google.cloud import storage
                self.gcs_client = storage.Client()
            return self._list_gcs_files(folder_path)
        else:
            logger.info(f"Detected local path: {folder_path}")
            return self._list_local_files(folder_path)

    def _list_local_files(self, folder_path: str) -> List[Dict[str, Any]]:
        files = []
        if not os.path.isdir(folder_path):
            raise FileNotFoundError(f"Local directory not found: {folder_path}")
        
        for filename in os.listdir(folder_path):
            filepath = os.path.join(folder_path, filename)
            if os.path.isfile(filepath):
                files.append({
                    "filename": filename,
                    "filepath": filepath,
                    "read_bytes": lambda p=filepath: self._read_local_file(p)
                })
        return files

    def _read_local_file(self, filepath: str) -> bytes:
        with open(filepath, "rb") as f:
            return f.read()

    def _list_gcs_files(self, gcs_path: str) -> List[Dict[str, Any]]:
        files = []
        bucket_name, prefix = self._parse_gcs_path(gcs_path)
        bucket = self.gcs_client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)

        for blob in blobs:
            if not blob.name.endswith('/'): # Skip "folders"
                filename = os.path.basename(blob.name)
                files.append({
                    "filename": filename,
                    "filepath": f"gs://{bucket_name}/{blob.name}",
                    "read_bytes": lambda b=blob: self._read_gcs_file(b)
                })
        return files

    def _read_gcs_file(self, blob) -> bytes:
        return blob.download_as_bytes()
    
    def _parse_gcs_path(self, gcs_path: str) -> Tuple[str, str]:
        path_parts = gcs_path.replace('gs://', '').split('/', 1)
        bucket_name = path_parts[0]
        prefix = path_parts[1] if len(path_parts) > 1 else ''
        return bucket_name, prefix
