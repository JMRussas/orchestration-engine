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

    def test_warns_on_model_pricing_mismatch(self, caplog):
        """Models configured without pricing entries should trigger a warning."""
        import logging
        with patch("backend.config.AUTH_SECRET_KEY", "a" * 32), \
             patch("backend.config.ANTHROPIC_API_KEY", "sk-test"), \
             patch("backend.config.cfg") as mock_cfg:
            def cfg_side_effect(path, default=None):
                return {
                    "model_pricing": {},
                    "anthropic.planning_model": "claude-sonnet-4-6",
                    "anthropic.models": {"haiku": "claude-haiku-4-5-20251001"},
                }.get(path, default)
            mock_cfg.side_effect = cfg_side_effect
            with caplog.at_level(logging.WARNING):
                validate_config()
            assert "claude-sonnet-4-6" in caplog.text
            assert "claude-haiku-4-5-20251001" in caplog.text
            assert "no entry in model_pricing" in caplog.text
