# AI Trade Document Classification Service

**Version:** 1.2.0

A FastAPI-based microservice to automatically cluster, classify, and sequence document pages from a disorganized folder using a vision-language AI model.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Workflow](#workflow)
- [API Endpoints](#api-endpoints)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [Installation](#installation)
- [Running the Service](#running-the-service)
- [Logging](#logging)

## Overview

This service addresses a critical challenge in Trade Finance document processing: a customer submits a collection of scanned pages and images in a jumbled, disorganized manner. These pages may belong to multiple distinct documents, such as Customer Request Letters (CRL), Invoices, Purchase Orders, and various declarations.

This microservice acts as an intelligent "Inputter" at the beginning of the workflow. Its mission is to take this chaotic collection of individual pages and transform it into a set of perfectly organized, classified, and correctly ordered digital documents, ready for the next stage of processing. It uses a powerful AI vision model to understand the content, layout, and context of each page to perform this task with high accuracy.

## Features

- **Automated Document Clustering:** Intelligently groups scattered pages into coherent, multi-page documents.
- **AI-Powered Classification:** Identifies document types (e.g., `CRL`, `INVOICE`, `UNKNOWN`) based on deep contextual understanding.
- **Accurate Page Sequencing:** Arranges the pages within each document into the correct logical order.
- **Versatile Input Handling:** Processes both PDF files and common image formats (e.g., PNG, JPEG).
- **High-Quality Preprocessing:** Converts PDF pages to high-resolution images, automatically distinguishing between digital-native and scanned content to optimize quality for the AI model.
- **Robust RESTful API:** Provides a simple and reliable endpoint for initiating the document processing workflow.
- **Structured JSON Logging:** Generates detailed, machine-readable logs for effective monitoring, auditing, and debugging.
- **Pluggable AI Backend:** Built with a provider interface (`AIProviderInterface`) to easily swap AI backends (currently implemented for OpenAI-compatible APIs).

## Workflow

1.  **API Request:** A client sends a POST request to the `/v1/documents/process-folder` endpoint, providing the path to a folder containing the document files.
2.  **Orchestration:** The `WorkflowService` receives the request and initiates the processing job.
3.  **Preprocessing:** The `DocumentProcessor` reads all files in the target folder.
   - PDF files are split into individual pages and converted into high-DPI images.
   - Existing image files are loaded and standardized into a consistent format.
4.  **AI Analysis:** The collection of page images is sent to the configured `AIProvider` (e.g., `OpenAIProvider`). A detailed, context-rich prompt instructs a vision-language model to perform three key tasks:
   - **Cluster:** Group related pages.
   - **Classify:** Assign a definitive type to each document cluster.
   - **Sequence:** Order the pages within each cluster.
5.  **Structured Response:** The AI model returns a structured JSON object containing the organized documents.
6.  **API Response:** The service formats the AI's output into a `ClassifiedDocumentsResponse` and sends it back to the client.

## API Endpoints

### Process Document Folder

Triggers the clustering, classification, and sequencing of all documents in a specified folder.

- **Endpoint:** `POST /v1/documents/process-folder`
- **Request Body:**

  ```json
  {
    "folder_path": "/path/to/your/document_folder",
    "srn_no": "SRN12345678"
  }
  ```

- **Success Response (200 OK):**

  ```json
  {
    "request": {
      "folder_path": "/path/to/your/document_folder",
      "srn_no": "SRN12345678"
    },
    "job_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
    "documents": [
      {
        "document_id": "doc_1",
        "document_type": "CRL",
        "pages": [
          "crl_page_1.png",
          "crl_page_2.png",
          "crl_page_3.png"
        ],
        "confidence_score": "High"
      },
      {
        "document_id": "doc_2",
        "document_type": "INVOICE",
        "pages": [
          "invoice_page_1.png"
        ],
        "confidence_score": "Very High"
      }
    ],
    "processing_metadata": {
      "ai_call_latency_ms": 15234.56,
      "token_usage": {
        "prompt_tokens": 1200,
        "completion_tokens": 550,
        "total_tokens": 1750
      }
    }
  }
  ```

## Prerequisites

- Python 3.9+
- `pip` for package management

## Configuration

The service is configured using environment variables. Create a `.env` file in the project root for local development.

| Variable                  | Description                                            | Default Value                 |
| ------------------------- | ------------------------------------------------------ | ----------------------------- |
| `AI_PROVIDER`             | The AI backend to use.                                 | `openai`                      |
| `OPENAI_API_KEY`          | Your API key for the OpenAI-compatible service.        | `your_api_key_here`           |
| `OPENAI_BASE_URL`         | The base URL for the AI provider's API.                | `https://api.openai.com/v1`   |
| `MODEL_NAME`              | The specific model to use for the analysis.            | `gpt-4-vision-preview`        |
| `TEMPERATURE`             | The sampling temperature for the model (0.0 to 2.0).   | `0.1`                         |
| `TOP_P`                   | The top-p nucleus sampling value.                      | `0.1`                         |
| `MAX_COMPLETION_TOKENS`   | The maximum number of tokens for the model to generate.| `4096`                        |
| `LOG_LEVEL`               | The application's logging level.                       | `INFO`                        |
| `LOG_FILE_PATH`           | Path to the rotating log file.                         | `./logs/app.log`              |
| `TARGET_DPI`              | The target DPI for converting PDF pages to images.     | `300`                         |
| `DEFAULT_DPI`             | The default DPI assumed for source images.             | `72`                          |

## Installation

1.  **Clone the repository:**
    ```sh
    git clone https://github.hdfcbank.com/HDFCBANK/ef-pf-ai-document-classification-service.git
    cd ef-pf-ai-document-classification-service
    ```

2.  **Create and activate a virtual environment:**
    ```sh
    python -m venv venv
    source venv/bin/activate
    # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```sh
    pip install -r requirements.txt
    ```

4.  **Set up configuration:**
    Create a `.env` file in the project root and populate it with the necessary values from the Configuration section.

## Running the Service

To run the application locally, use `uvicorn`:

```sh
uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`. Interactive API documentation (Swagger UI) can be accessed at `http://127.0.0.1:8000/docs`.

## Logging

The application is configured with structured logging using `python-json-logger`.

- **Console Logs:** For local development, logs are printed to the console in a human-readable format.
- **File Logs:** Logs are also written to a rotating file in JSON format at the path specified by the `LOG_FILE_PATH` environment variable (default: `./logs/app.log`). This is ideal for log aggregation and analysis in a production environment.

