import pytest
from unittest.mock import patch, MagicMock, call
from PIL import Image
import base64

from app.services.document_processor import DocumentProcessor


@pytest.fixture
def mock_settings():
    """Fixture to mock application settings."""
    with patch("app.services.document_processor.settings") as mock:
        mock.TARGET_DPI = 200
        mock.DEFAULT_DPI = 72
        mock.DEFAULT_IMAGE_FORMAT = "png"
        yield mock


@pytest.fixture
def processor(mock_settings):
    """Fixture to get an instance of DocumentProcessor."""
    return DocumentProcessor()


@pytest.fixture
def mock_pymupdf():
    """Fixture to mock the entire pymupdf library."""
    with patch("app.services.document_processor.pymupdf") as mock_pymupdf_lib:
        # Mock the Page and other necessary components
        mock_page = MagicMock()
        mock_page.get_text.return_value = "some digital text"

        mock_pixmap = MagicMock()
        mock_pixmap.width = 800
        mock_pixmap.height = 600
        mock_pixmap.samples = b'\x00' * (800 * 600 * 3)  # Dummy RGB data

        mock_page.get_pixmap.return_value = mock_pixmap

        mock_doc = MagicMock()
        mock_doc.__iter__.return_value = [mock_page]

        mock_pymupdf_lib.open.return_value = mock_doc
        mock_pymupdf_lib.Matrix = MagicMock()

        yield mock_pymupdf_lib


@pytest.fixture
def mock_fs_util():
    """Fixture to mock the FileSystemUtil."""
    with patch("app.services.document_processor.FileSystemUtil") as mock_fs:
        yield mock_fs.return_value


def create_test_image(width=10, height=10, mode="RGB", color="blue"):
    """Helper to create a real PIL Image for testing."""
    return Image.new(mode, (width, height), color)


def test_initialization(processor, mock_settings):
    """Tests that the processor initializes its attributes from settings."""
    assert processor.TARGET_DPI == mock_settings.TARGET_DPI
    assert processor.DEFAULT_DPI == mock_settings.DEFAULT_DPI
    assert processor.DEFAULT_IMAGE_FORMAT == mock_settings.DEFAULT_IMAGE_FORMAT


@patch("app.services.document_processor.mimetypes")
def test_get_file_mime_type(mock_mimetypes, processor):
    """Tests MIME type detection."""
    # Arrange
    mock_mimetypes.guess_type.return_value = ("application/pdf", None)

    # Act
    mime_type = processor.get_file_mime_type("test.pdf")

    # Assert
    assert mime_type == "application/pdf"
    mock_mimetypes.guess_type.assert_called_once_with("test.pdf")


def test_classify_page(processor):
    """Tests page classification based on text content."""
    # Arrange
    mock_digital_page = MagicMock()
    mock_digital_page.get_text.return_value = "This is a digital page."

    mock_scanned_page = MagicMock()
    mock_scanned_page.get_text.return_value = "  \t\n"  # Whitespace only

    # Act & Assert
    assert processor.classify_page(mock_digital_page) == "digital"
    assert processor.classify_page(mock_scanned_page) == "scanned"


def test_image_conversion(processor):
    """Tests conversion of a PIL Image to bytes and base64."""
    # Arrange
    image = create_test_image()

    # Act
    img_bytes = processor.convert_image_to_bytes(image, format="png")
    img_b64 = processor.convert_image_to_base64_string(image, format="png")

    # Assert
    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 0
    assert isinstance(img_b64, str)
    # Check if the base64 string is valid by decoding it
    decoded_bytes = base64.b64decode(img_b64)
    assert decoded_bytes == img_bytes


def test_get_image_metadata(processor):
    """Tests metadata extraction from a PIL Image."""
    # Arrange
    image = create_test_image(width=100, height=50)

    # Act
    metadata = processor.get_image_metadata(image)

    # Assert
    assert metadata["width"] == 100
    assert metadata["height"] == 50
    assert metadata["mode"] == "RGB"
    assert "brightness_mean" in metadata
    assert "contrast_std" in metadata


@patch("app.services.document_processor.Image.frombytes")
def test_process_pdf_to_images(mock_frombytes, processor, mock_pymupdf):
    """Tests the PDF to image conversion process."""
    # Arrange
    pdf_bytes = b"fake-pdf-content"
    # Configure the mock image to have an 'info' dictionary attribute
    mock_image = MagicMock(spec=Image.Image)
    mock_image.info = {}
    mock_frombytes.return_value.convert.return_value = mock_image

    # Act
    images_data = processor.process_pdf_to_images(pdf_bytes, target_dpi=200, default_dpi=72)

    # Assert
    mock_pymupdf.open.assert_called_once_with(stream=pdf_bytes, filetype="pdf")
    assert len(images_data) == 1
    result = images_data[0]
    assert result["page_number"] == 1
    assert result["classification"] == "digital"
    assert isinstance(result["image"], MagicMock)

    # Verify that the correct scaling matrix was used for a digital page
    scaling_factor = 200 / 72
    mock_pymupdf.Matrix.assert_called_with(scaling_factor, scaling_factor)


@patch("app.services.document_processor.Image.open")
def test_preprocess_folder_with_pdf_and_image(mock_image_open, processor, mock_fs_util, mock_pymupdf):
    """
    Tests the main `preprocess_folder` method with a mix of file types.
    """
    # Arrange
    # --- Mock FileSystemUtil ---
    pdf_file_info = {
        "filename": "test.pdf",
        "filepath": "/fake/test.pdf",
        "read_bytes": MagicMock(return_value=b"fake-pdf-bytes")
    }
    img_file_info = {
        "filename": "test.jpg",
        "filepath": "/fake/test.jpg",
        "read_bytes": MagicMock(return_value=b"fake-jpg-bytes")
    }
    mock_fs_util.list_files.return_value = [pdf_file_info, img_file_info]

    # --- Mock mimetypes ---
    def guess_type_side_effect(path):
        if path == "test.pdf":
            return ("application/pdf", None)
        if path == "test.jpg":
            return ("image/jpeg", None)
        return (None, None)

    # --- Mock Image.open for the JPG file ---
    mock_pil_image = create_test_image()
    mock_image_open.return_value.convert.return_value = mock_pil_image

    with patch("app.services.document_processor.mimetypes") as mock_mimetypes:
        mock_mimetypes.guess_type.side_effect = guess_type_side_effect

        # Act
        documents = processor.preprocess_folder("/fake/folder")

    # Assert
    assert len(documents) == 2
    mock_fs_util.list_files.assert_called_once_with("/fake/folder")

    # --- Assert PDF processing ---
    pdf_doc = documents[0]
    assert pdf_doc["filename"] == "test.pdf"
    assert pdf_doc["filemimetype"] == "application/pdf"
    assert len(pdf_doc["images"]) == 1
    assert "data" in pdf_doc["images"][0]  # Base64 data
    assert "metadata" in pdf_doc["images"][0]
    pdf_file_info["read_bytes"].assert_called_once()
    mock_pymupdf.open.assert_called_once()

    # --- Assert Image processing ---
    img_doc = documents[1]
    assert img_doc["filename"] == "test.jpg"
    assert img_doc["filemimetype"] == "image/jpeg"
    assert len(img_doc["images"]) == 1
    assert img_doc["images"][0]["page_number"] == 1
    assert img_doc["images"][0]["classification"] == "scanned"
    assert "data" in img_doc["images"][0]
    assert "metadata" in img_doc["images"][0]
    img_file_info["read_bytes"].assert_called_once()
    mock_image_open.assert_called_once()


def test_preprocess_folder_not_found(processor, mock_fs_util):
    """Tests that FileNotFoundError is propagated from FileSystemUtil."""
    # Arrange
    mock_fs_util.list_files.side_effect = FileNotFoundError("Directory not found")

    # Act & Assert
    with pytest.raises(FileNotFoundError, match="Directory not found"):
        processor.preprocess_folder("/non/existent/folder")
