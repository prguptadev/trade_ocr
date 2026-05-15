import os
import unittest
from unittest.mock import patch
from pydantic import ValidationError

# Assuming config.py is in the same directory or accessible via PYTHONPATH
from app.config import Settings

class TestConfig(unittest.TestCase):

    def test_settings_load_from_env(self):
        """
        Tests that settings are correctly loaded from environment variables.
        """
        mock_env = {
            "OPENAI_API_KEY": "test_api_key_from_env",
            "OPENAI_BASE_URL": "http://localhost:8080",
            "LOG_LEVEL": "DEBUG"
        }

        with patch.dict(os.environ, mock_env):
            # We must tell Settings NOT to load any .env file for this test
            settings = Settings(_env_file=None)
            self.assertEqual(settings.OPENAI_API_KEY, "test_api_key_from_env")
            self.assertEqual(settings.OPENAI_BASE_URL, "http://localhost:8080")
            self.assertEqual(settings.LOG_LEVEL, "DEBUG")

    def test_settings_missing_required_env_vars(self):
        """
        Tests that a ValidationError is raised if required env vars and .env files are missing.
        """
        # Ensure the process environment variables are not set
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                # CRITICAL FIX: Explicitly disable .env file loading for this test instance.
                # This ensures Pydantic only checks the (now empty) environment.
                Settings(_env_file=None)

    def test_settings_default_values(self):
        """
        Tests that default values are used when environment variables are not provided.
        """
        mock_env = {
            "OPENAI_API_KEY": "fake-key",
            "OPENAI_BASE_URL": "http://fake-url"
        }
        with patch.dict(os.environ, mock_env, clear=True):
            settings = Settings(_env_file=None)
            # Test default values
            self.assertEqual(settings.AI_PROVIDER, "openai")
            self.assertEqual(settings.MODEL_NAME, "gemini-2.5-flash")
            self.assertEqual(settings.TEMPERATURE, 0.01)
            self.assertEqual(settings.LOG_LEVEL, "INFO")
            self.assertEqual(settings.LOG_ROTATION_MAX_BYTES, 10485760)

if __name__ == '__main__':
    unittest.main()
