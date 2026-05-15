import os
import io
import pymupdf
import logging
import mimetypes
import base64
import numpy

from PIL import Image
from typing import List, Dict

from ..config import settings
from ..utils.file_system_utils import FileSystemUtil

logger = logging.getLogger(__name__)

class DocumentProcessor:
    
    """
    Handles all document preprocessing tasks, including PDF-to-image conversion
    and image enhancement.
    """

    def __init__(self):
        self.TARGET_DPI = settings.TARGET_DPI
        self.DEFAULT_DPI = settings.DEFAULT_DPI
        self.SMALL_ANGLE_THRESHOLD = settings.SMALL_ANGLE_THRESHOLD
        self.DEFAULT_IMAGE_FORMAT = settings.DEFAULT_IMAGE_FORMAT
        self.SHARPEN_CONTRAST_ALPHA = settings.SHARPEN_CONTRAST_ALPHA
        self.SHARPEN_CONTRAST_BETA = settings.SHARPEN_CONTRAST_BETA

    def get_file_mime_type(self, file_path: str) -> str:
        """
        Get the MIME type of a file based on its path.
        """
        mime_type, _ = mimetypes.guess_type(file_path)
        detected_mime_type = mime_type or 'application/octet-stream'
        logger.debug("get_file_mime_type: For file_path %s, detected MIME type: %s", file_path, detected_mime_type)
        return detected_mime_type
    
    def classify_page(self, page: pymupdf.Page) -> str:
        """
        Coroutine to classify a PDF page as digital (contains extractable text)
        or scanned (image-based).
        """
        text = page.get_text("text")
        classification_result = "digital" if text.strip() else "scanned"
        logger.debug("classify_page: Page classified as %s", classification_result)
        return classification_result

    def convert_image_to_bytes (self, image: Image.Image, format: str="png") -> bytes:
        """
        Convert a PIL Image to bytes.
        """
        logger.debug("Converting image to bytes with format: %s", format)
        buffer = io.BytesIO()
        image.save(buffer, format=format)
        return buffer.getvalue()

    def convert_image_to_base64_string(self, image: Image.Image, format: str = "png") -> str:
        """
        Convert a PIL Image to a Base64 encoded string.
        """
        logger.debug("Converting image to base64 string with format: %s", format)
        # First, get the raw binary bytes using your original function
        raw_bytes = self.convert_image_to_bytes(image, format=format)
        # Now, encode these raw bytes into Base64
        base64_bytes = base64.b64encode(raw_bytes)
        # Decode the Base64 bytes into a string for easy use (e.g., in JSON)
        base64_string = base64_bytes.decode("utf-8")
        return base64_string

    def get_image_metadata(self, image: Image.Image) -> dict:
        """
        Extract metadata and basic statistics from a PIL Image object.
        """
        logger.debug("Extracting image metadata.")
        # Basic image properties
        width, height = image.size
        mode = image.mode
        channels = len(image.getbands())
        dpi = image.info.get('dpi', None)

        # Convert to grayscale for brightness/contrast metrics
        gray = image.convert('L')
        arr = numpy.array(gray, dtype=numpy.float32)
        mean_val = float(arr.mean())
        std_val = float(arr.std())
        min_val = int(arr.min())
        max_val = int(arr.max())
        dyn_range = max_val - min_val

        return {
            'width': width,
            'height': height,
            'mode': mode,
            'channels': channels,
            'dpi': dpi,
            'brightness_mean': mean_val,
            'contrast_std': std_val,
            'min_intensity': min_val,
            'max_intensity': max_val,
            'dynamic_range': dyn_range,
        }


    def process_pdf_to_images (self, pdf_bytes: bytes, target_dpi: int=300, default_dpi: int=72) -> list[Image.Image]:
        """
        Process a PDF file, converting each page to an image.
        Digital pages are classified as PNG and scanned as JPEG (for demonstration).
        Returns a list of PIL Images.
        """
        logger.info("Starting PDF to image conversion with target_dpi=%d, default_dpi=%d", target_dpi, default_dpi)
        images = []
        scaling_factor = target_dpi / default_dpi
        logger.debug("Calculated scaling_factor: %s", scaling_factor)
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        for page_num, page in enumerate(doc):
            dpi_for_tag = None
            classification = self.classify_page (page)
            # Use classification to choose render resolution
            if classification == "digital":
                # Render digital pages at 2x resolution for clarity
                matrix = pymupdf.Matrix(scaling_factor, scaling_factor)
                dpi_for_tag = target_dpi
                logger.debug("Page %d is digital, setting dpi_for_tag to %s", page_num + 1, dpi_for_tag)
            else:
                # Render scanned pages at default resolution
                matrix = pymupdf.Matrix(1, 1)
                logger.debug(f"Page {page_num+1} is scanned, using default matrix (1,1)")

            pix = page.get_pixmap(matrix=matrix)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            img = img.convert("RGB")

            if dpi_for_tag:
                # **tag the DPI** so subsequent code can read image.info['dpi']
                img.info["dpi"] = (dpi_for_tag, dpi_for_tag)
                logger.debug("Page %d: Tagged image with DPI: %s", page_num + 1, dpi_for_tag)
            else:
                if (img.width * img.height) >= 1336 * 768:
                    logger.info("INFO: Image resolution is on the higher side. Setting target DPI")
                    img.info["dpi"] = (target_dpi, target_dpi)
                    logger.debug("Page %d: Image resolution high, setting DPI to target_dpi: %d", page_num + 1, target_dpi)
                else:
                    img.info["dpi"] = (default_dpi, default_dpi)
                    logger.debug("Page %d: Image resolution normal, setting DPI to default_dpi: %d", page_num + 1, default_dpi)

            images.append({
                "page_number": page_num + 1,
                "classification": classification,
                "image": img
            })
            logger.info("Converted page %d as %s image (size: %dx%d).", page_num + 1, classification, pix.width, pix.height)
        doc.close()
        return images

    def preprocess_folder(self, data_folder: str) -> List[Dict]:
        logger.info("Starting preprocessing for folder: %s", data_folder)
        documents = []
        fs_util = FileSystemUtil()
        
        try:
            files_to_process = fs_util.list_files(data_folder)
            logger.info("Found %d files to process in %s", len(files_to_process), data_folder)
        except FileNotFoundError as e:
            logger.error("Directory or GCS path not found: %s", e)
            raise # Re-raise the exception to be caught by the main endpoint

        for file_info in files_to_process:
            filename = file_info["filename"]
            filepath = file_info["filepath"]
            
            # Get the mime type
            file_mime_type = self.get_file_mime_type(file_path=filename) # Use filename for mime guess
            logger.info("Determined MIME type for %s: %s", filename, file_mime_type)

            # Read file bytes using the utility's function
            file_bytes = file_info["read_bytes"]()
            logger.debug(f"Read bytes for file: {filename}")

            # If the mime type is PDF
            if file_mime_type == "application/pdf":
                logger.info("Processing %s as a PDF file. Converting pages to images.", filename)
                file_images = self.process_pdf_to_images(
                    pdf_bytes=file_bytes, 
                    target_dpi=self.TARGET_DPI, 
                    default_dpi=self.DEFAULT_DPI
                )
                images = []
                for image_json in file_images:
                    image = image_json["image"]
                    logger.info("INFO: Image pre-processing and enhancement complete for %s, page number %d", filename, image_json['page_number'])
                    image_bytes_b64 = self.convert_image_to_base64_string(image=image, format=self.DEFAULT_IMAGE_FORMAT)
                    images.append({
                        "data": image_bytes_b64,
                        "mime_type": f"image/{self.DEFAULT_IMAGE_FORMAT}",
                        "page_number": image_json["page_number"],
                        "classification": image_json["classification"],
                        "metadata": self.get_image_metadata(image)
                    })
                documents.append({
                    "filename": filename,
                    "filepath": filepath,
                    "filemimetype": file_mime_type,
                    "images": images
                })
                logger.info("Appended processed PDF document for %s with %d images. Confirmed MIME type: %s", filename, len(images), file_mime_type)
            
            elif file_mime_type.startswith("image/"):
                logger.info("Processing %s as an image file.", filename)
                image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
                logger.info("INFO: Image pre-processing and enhancement complete for %s", filename)
                image_bytes_b64 = self.convert_image_to_base64_string(image=image, format=self.DEFAULT_IMAGE_FORMAT)
                documents.append({
                    "filename": filename,
                    "filepath": filepath,
                    "filemimetype": file_mime_type,
                    "images": [{
                        "data": image_bytes_b64,
                        "mime_type": f"image/{self.DEFAULT_IMAGE_FORMAT}",
                        "page_number": 1,
                        "classification": "scanned",
                        "metadata": self.get_image_metadata(image)
                    }]
                })
                logger.info("Appended processed image document for %s. Confirmed MIME type: %s", filename, file_mime_type)
        
        return documents