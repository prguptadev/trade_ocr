# Tradeflow - AI Document Extraction Service

[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Framework](https://img.shields.io/badge/Framework-FastAPI-green.svg)](https://fastapi.tiangolo.com/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

A high-performance API service for extracting structured data from documents using cutting-edge AI models. This service is a component of the larger Tradeflow application.

## Overview

The AI Document Extraction Service provides a RESTful API to submit documents and receive structured JSON data in return. It integrates with OpenAI's powerful models to perform analysis and data extraction, making it a crucial component for automating document processing workflows within Tradeflow.

## Features

-   **High-Performance API**: Built with [FastAPI](https://fastapi.tiangolo.com/) and Uvicorn for asynchronous, high-speed request handling.
-   **State-of-the-Art AI Integration**: Leverages the [OpenAI API](https://platform.openai.com/docs) for intelligent document understanding.
-   **Structured Data Output**: Returns clean, validated, and structured JSON data using [Pydantic](https://docs.pydantic.dev/) models.
-   **Configuration-driven**: Easily configurable through environment variables for different deployment environments.
-   **Interactive API Docs**: Automatic generation of interactive API documentation (Swagger UI and ReDoc).

## Tech Stack

-   **Backend**: Python, FastAPI
-   **Web Server**: Uvicorn
-   **AI Integration**: OpenAI Python SDK
-   **HTTP Client**: HTTPX
-   **Data Validation**: Pydantic
-   **Configuration**: Pydantic-Settings, python-dotenv

## Getting Started

Follow these instructions to get a copy of the project up and running on your local machine for development and testing purposes.

### Prerequisites

-   Python 3.9+
-   `pip` and `venv`
-   Git

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.hdfcbank.com/HDFCBANK/ef-pf-ai-document-extraction-service.git
    cd ef-pf-ai-document-extraction-service
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    # For macOS/Linux
    python3 -m venv venv
    source venv/bin/activate

    # For Windows
    python -m venv venv
    .\venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

The application is configured using environment variables. Create a `.env` file in the project root directory. It's good practice to have a `.env.example` file in the repository to show the required variables.

1.  **Create a `.env` file:**
    You can copy an example file if it exists:
    `cp .env.example .env`

2.  **Populate the `.env` file:**
    You will need to add your credentials and other configuration settings. The most important one is your OpenAI API key.

    ```dotenv
    # .env

    # OpenAI Configuration
    OPENAI_API_KEY="your-openai-api-key-here"

    # Server Configuration (Optional)
    APP_HOST="0.0.0.0"
    APP_PORT=8000
    ```

## Running the Application

Once the dependencies are installed and the configuration is set up, you can run the application using Uvicorn.

```bash
uvicorn app.main:app --reload
```
*(This command assumes your main FastAPI application instance is named `app` inside a `main.py` file. Adjust if necessary.)*

The API will be available at `http://127.0.0.1:8000`.

## API Documentation

This service comes with auto-generated, interactive API documentation thanks to FastAPI. Once the application is running, you can access it at:

-   **Swagger UI**: `http://127.0.0.1:8000/docs`
-   **ReDoc**: `http://127.0.0.1:8000/redoc`

These interfaces provide detailed information about all available endpoints, their parameters, and allow you to interact with the API directly from your browser.

## Project Information

-   **ITGRC Application Name**: Tradeflow
-   **ITGRC App ID**: APPID-2387310
-   **Project Owner**: RAVINANDAN MULLAHALLI ESWARAMURTHY
-   **Application Lead**: EF_PF_AMPS_BE_GITHUB_ADMIN_USERS
-   **SNOW Ticket**: RITM4641018

