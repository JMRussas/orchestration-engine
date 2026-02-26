#  Orchestration Engine - Config Validation Tests
#
#  Tests for validate_config() startup checks.
#
#  Depends on: backend/config.py
#  Used by:    pytest

import pytest
from unittest.mock import patch

from backend.config import ConfigError, validate_config


class TestValidateConfig:
    def test_raises_on_empty_secret(self):
        with patch("backend.config.AUTH_SECRET_KEY", ""):
            with pytest.raises(ConfigError, match="missing or too short"):
                validate_config()

    def test_raises_on_short_secret(self):
        with patch("backend.config.AUTH_SECRET_KEY", "tooshort"):
            with pytest.raises(ConfigError, match="missing or too short"):
                validate_config()

    def test_passes_with_valid_secret(self):
        with patch("backend.config.AUTH_SECRET_KEY", "a" * 32):
            with patch("backend.config.ANTHROPIC_API_KEY", "sk-test"):
                validate_config()  # should not raise

    def test_warns_on_missing_anthropic_key(self, caplog):
        with patch("backend.config.AUTH_SECRET_KEY", "a" * 32):
            with patch("backend.config.ANTHROPIC_API_KEY", ""):
                import logging
                with caplog.at_level(logging.WARNING):
                    validate_config()
                assert "ANTHROPIC_API_KEY is not set" in caplog.text
