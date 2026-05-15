"""Test cases for the helpers utility module."""
import pytest
import io
import base64
from PIL import Image
from unittest.mock import MagicMock, patch, mock_open
import pymupdf
from app.utils.helpers import (
    get_mime_type,
    classify_page,
    convert_image_to_bytes,
    convert_image_to_base64_string,
    process_pdf_to_images,
)


# =====================================================================
# TESTS FOR get_mime_type
# =====================================================================

def test_get_mime_type_pdf():
    """Tests MIME type detection for PDF files."""
    # Arrange
    file_path = "document.pdf"
    
    # Act
    result = get_mime_type(file_path)
    
    # Assert
    assert result == "application/pdf"


def test_get_mime_type_image_png():
    """Tests MIME type detection for PNG images."""
    # Arrange
    file_path = "image.png"
    
    # Act
    result = get_mime_type(file_path)
    
    # Assert
    assert result == "image/png"


def test_get_mime_type_image_jpg():
    """Tests MIME type detection for JPG images."""
    # Arrange
    file_path = "photo.jpg"
    
    # Act
    mime = get_mime_type(file_path)
    
    # Assert
    assert mime in ["image/jpeg", "image/jpg"]


def test_get_mime_type_unsupported():
    """Tests MIME type detection for unsupported file types."""
    # Arrange
    file_path = "file.xyz"
    
    # Act
    result = get_mime_type(file_path)
    
    # Assert
    assert result == "application/octet-stream"


def test_get_mime_type_no_extension():
    """Tests MIME type detection for files without extension."""
    # Arrange
    file_path = "README"
    
    # Act
    result = get_mime_type(file_path)
    
    # Assert
    assert result == "application/octet-stream"


def test_get_mime_type_image_jpeg():
    """Tests MIME type detection for JPEG images."""
    # Arrange
    file_path = "image.jpeg"
    
    # Act
    mime = get_mime_type(file_path)
    
    # Assert
    assert mime in ["image/jpeg", "image/jpg"]


def test_get_mime_type_text_file():
    """Tests MIME type detection for unsupported text files."""
    # Arrange
    file_path = "document.txt"
    
    # Act
    result = get_mime_type(file_path)
    
    # Assert
    assert result == "application/octet-stream"


# =====================================================================
# TESTS FOR classify_page
# =====================================================================

def test_classify_page_digital_with_text():
    """Tests classification of digital page (with text)."""
    # Arrange
    mock_page = MagicMock(spec=pymupdf.Page)
    mock_page.get_text.return_value = "This is some text content"
    
    # Act
    result = classify_page(mock_page)
    
    # Assert
    assert result == "digital"


def test_classify_page_scanned_without_text():
    """Tests classification of scanned page (no text)."""
    # Arrange
    mock_page = MagicMock(spec=pymupdf.Page)
    mock_page.get_text.return_value = ""
    
    # Act
    result = classify_page(mock_page)
    
    # Assert
    assert result == "scanned"


def test_classify_page_scanned_whitespace_only():
    """Tests classification of page with only whitespace."""
    # Arrange
    mock_page = MagicMock(spec=pymupdf.Page)
    mock_page.get_text.return_value = "   \n\t  "
    
    # Act
    result = classify_page(mock_page)
    
    # Assert
    assert result == "scanned"


def test_classify_page_digital_multiline_text():
    """Tests classification of page with multiline text."""
    # Arrange
    mock_page = MagicMock(spec=pymupdf.Page)
    mock_page.get_text.return_value = "Line 1\nLine 2\nLine 3"
    
    # Act
    result = classify_page(mock_page)
    
    # Assert
    assert result == "digital"


# =====================================================================
# TESTS FOR convert_image_to_bytes
# =====================================================================

def test_convert_image_to_bytes_png():
    """Tests converting PIL Image to PNG bytes."""
    # Arrange
    img = Image.new("RGB", (100, 100), color="red")
    
    # Act
    result = convert_image_to_bytes(img, format="png")
    
    # Assert
    assert isinstance(result, bytes)
    assert len(result) > 0
    assert result.startswith(b"\x89PNG")


def test_convert_image_to_bytes_jpeg():
    """Tests converting PIL Image to JPEG bytes."""
    # Arrange
    img = Image.new("RGB", (100, 100), color="blue")
    
    # Act
    result = convert_image_to_bytes(img, format="jpeg")
    
    # Assert
    assert isinstance(result, bytes)
    assert len(result) > 0
    assert result.startswith(b"\xff\xd8\xff")


def test_convert_image_to_bytes_default_format():
    """Tests converting PIL Image with default PNG format."""
    # Arrange
    img = Image.new("RGB", (50, 50), color="green")
    
    # Act
    result = convert_image_to_bytes(img)
    
    # Assert
    assert isinstance(result, bytes)
    assert result.startswith(b"\x89PNG")


def test_convert_image_to_bytes_large_image():
    """Tests converting large PIL Image."""
    # Arrange
    img = Image.new("RGB", (1000, 1000), color="yellow")
    
    # Act
    result = convert_image_to_bytes(img, format="png")
    
    # Assert
    assert isinstance(result, bytes)
    assert len(result) > 1000


# =====================================================================
# TESTS FOR convert_image_to_base64_string
# =====================================================================

def test_convert_image_to_base64_string_png():
    """Tests converting PIL Image to base64 PNG string."""
    # Arrange
    img = Image.new("RGB", (100, 100), color="red")
    
    # Act
    result = convert_image_to_base64_string(img, format="png")
    
    # Assert
    assert isinstance(result, str)
    decoded = base64.b64decode(result)
    assert decoded.startswith(b"\x89PNG")


def test_convert_image_to_base64_string_jpeg():
    """Tests converting PIL Image to base64 JPEG string."""
    # Arrange
    img = Image.new("RGB", (100, 100), color="blue")
    
    # Act
    result = convert_image_to_base64_string(img, format="jpeg")
    
    # Assert
    assert isinstance(result, str)
    decoded = base64.b64decode(result)
    assert decoded.startswith(b"\xff\xd8\xff")


def test_convert_image_to_base64_string_default_format():
    """Tests converting PIL Image to base64 with default format."""
    # Arrange
    img = Image.new("RGB", (50, 50), color="green")
    
    # Act
    result = convert_image_to_base64_string(img)
    
    # Assert
    assert isinstance(result, str)
    decoded = base64.b64decode(result)
    assert decoded.startswith(b"\x89PNG")


def test_convert_image_to_base64_string_is_valid_base64():
    """Tests that result is valid base64 encoded string."""
    # Arrange
    img = Image.new("RGB", (10, 10), color="white")
    
    # Act
    result = convert_image_to_base64_string(img)
    
    # Assert
    decoded = base64.b64decode(result)
    assert isinstance(decoded, bytes)


# =====================================================================
# TESTS FOR process_pdf_to_images
# =====================================================================

def test_process_pdf_to_images_single_page():
    """Tests processing single-page PDF."""
    # Arrange
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Test PDF")
    pdf_bytes = doc.write()
    doc.close()
    
    # Act
    result = process_pdf_to_images(pdf_bytes, target_dpi=300)
    
    # Assert
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["page_number"] == 1
    assert result[0]["classification"] in ["digital", "scanned"]
    assert isinstance(result[0]["image"], Image.Image)


def test_process_pdf_to_images_multi_page():
    """Tests processing multi-page PDF."""
    # Arrange
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((50, 50), f"Page {i + 1}")
    pdf_bytes = doc.write()
    doc.close()
    
    # Act
    result = process_pdf_to_images(pdf_bytes)
    
    # Assert
    assert len(result) == 3
    for i, item in enumerate(result):
        assert item["page_number"] == i + 1
        assert "classification" in item
        assert "image" in item


def test_process_pdf_to_images_digital_classification():
    """Tests that digital PDFs are classified correctly."""
    # Arrange
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "This is digital text")
    pdf_bytes = doc.write()
    doc.close()
    
    # Act
    result = process_pdf_to_images(pdf_bytes, target_dpi=300)
    
    # Assert
    assert result[0]["classification"] == "digital"


def test_process_pdf_to_images_custom_dpi():
    """Tests processing PDF with custom DPI settings."""
    # Arrange
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Test")
    pdf_bytes = doc.write()
    doc.close()
    
    # Act
    result = process_pdf_to_images(pdf_bytes, target_dpi=150, default_dpi=72)
    
    # Assert
    assert len(result) == 1
    assert isinstance(result[0]["image"], Image.Image)


def test_process_pdf_to_images_returns_pil_images():
    """Tests that returned images are PIL Image objects."""
    # Arrange
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Test")
    pdf_bytes = doc.write()
    doc.close()
    
    # Act
    result = process_pdf_to_images(pdf_bytes)
    
    # Assert
    for item in result:
        assert isinstance(item["image"], Image.Image)
        assert item["image"].format in [None, "RGB"]


def test_process_pdf_to_images_image_dimensions():
    """Tests that returned images have proper dimensions."""
    # Arrange
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Test")
    pdf_bytes = doc.write()
    doc.close()
    
    # Act
    result = process_pdf_to_images(pdf_bytes, target_dpi=300)
    
    # Assert
    for item in result:
        width, height = item["image"].size
        assert width > 0
        assert height > 0


def test_process_pdf_to_images_empty_pdf():
    """Tests processing empty PDF."""
    # Arrange
    doc = pymupdf.open()
    page = doc.new_page()
    pdf_bytes = doc.write()
    doc.close()
    
    # Act
    result = process_pdf_to_images(pdf_bytes)
    
    # Assert
    assert isinstance(result, list)
    assert len(result) >= 0


def test_process_pdf_to_images_default_dpi_values():
    """Tests processing with default DPI values."""
    # Arrange
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Default DPI test")
    pdf_bytes = doc.write()
    doc.close()
    
    # Act
    result = process_pdf_to_images(pdf_bytes)
    
    # Assert
    assert len(result) > 0
    assert result[0]["page_number"] == 1