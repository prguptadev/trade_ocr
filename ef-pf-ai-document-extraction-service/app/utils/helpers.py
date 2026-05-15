# app/utils/helpers.py
import mimetypes
from app.core.config import settings
import pymupdf
import base64
import io
from typing import List, Dict
from PIL import Image
from app.core.logging_config import log

def get_mime_type(file_path: str) -> str:
    """Determines the MIME type of a file."""
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type if mime_type in settings.SUPPORTED_MIME_TYPES else "application/octet-stream"

def classify_page(page: pymupdf.Page) -> str:
    """Classifies a PDF page as 'digital' (has text) or 'scanned' (no text)."""
    text = page.get_text("text")
    return "digital" if text.strip() else "scanned"

def convert_image_to_bytes(image: Image.Image, format: str = "png") -> bytes:
    """Converts a PIL Image object to bytes."""
    with io.BytesIO() as buffer:
        image.save(buffer, format=format)
        return buffer.getvalue()

def convert_image_to_base64_string(image: Image.Image, format: str = "png") -> str:
    """Converts a PIL Image object to a Base64 encoded string."""
    raw_bytes = convert_image_to_bytes(image, format=format)
    base64_bytes = base64.b64encode(raw_bytes)
    return base64_bytes.decode("utf-8")

def process_pdf_to_images(pdf_bytes: bytes, target_dpi: int = 300, default_dpi: int = 72) -> List[Dict]:
    """Converts each page of a PDF into a list of image dictionaries."""
    images = []
    scaling_factor = target_dpi / default_dpi
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")

    for page_num, page in enumerate(doc):
        classification = classify_page(page)
        
        # Use a higher resolution for digital pages to ensure text clarity
        matrix = pymupdf.Matrix(scaling_factor, scaling_factor) if classification == "digital" else pymupdf.Matrix(1, 1)

        pix = page.get_pixmap(matrix=matrix, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        images.append({
            "page_number": page_num + 1,
            "classification": classification,
            "image": img
        })
        log.info(f"Converted PDF page {page_num + 1} as a '{classification}' image.")
    
    doc.close()
    return images