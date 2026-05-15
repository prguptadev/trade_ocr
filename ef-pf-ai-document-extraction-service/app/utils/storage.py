from typing import Optional
from google.api_core.exceptions import NotFound

def get_file_bytes(path: str) -> bytes:
    """
    Reads a file's content as bytes, supporting both local paths and GCS URIs.

    Args:
        path: The local file path or a GCS URI (e.g., "gs://bucket-name/file.png").

    Returns:
        The file content in bytes.

    Raises:
        FileNotFoundError: If the file does not exist at the local path or in GCS.
    """
    if path.startswith("gs://"):
        try:
            from google.cloud import storage  
            bucket_name, blob_name = path[5:].split("/", 1)
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            content = blob.download_as_bytes()
            return content
        except (NotFound, ValueError) as e:
            raise FileNotFoundError(f"File not found in GCS at path: {path}") from e
    else:
        # Fallback to local file system
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError as e:
            raise FileNotFoundError(f"File not found at local path: {path}") from e

