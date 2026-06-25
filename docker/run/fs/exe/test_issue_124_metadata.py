
import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Add the project root to sys.path
sys.path.append(os.getcwd())

# Import only what we need to test
from python.models import LiteLLMChatWrapper

class TestIssue124Metadata(unittest.TestCase):
    """
    TDD focused on verifying that LiteLLMChatWrapper returns user-friendly 
    provider and model names for the UI.
    """

    @patch('models.get_provider_config')
    def test_metadata_transformation_venice(self, mock_get_config):
        """Test Venice AI transformation (ID 'venice' -> 'Venice AI')"""
        # Mock the configuration lookup
        mock_get_config.return_value = {
            "name": "Venice AI",
            "litellm_provider": "openai"
        }

        # Initialize wrapper with 'venice' ID
        wrapper = LiteLLMChatWrapper(
            model="grok-41-fast",
            provider="venice"
        )
        
        provider_name = wrapper.display_provider
        model_name = wrapper.display_model
        
        # Expected:
        self.assertEqual(provider_name, "Venice AI", "Provider ID should be mapped to its Display Name")
        self.assertEqual(model_name, "grok-41-fast", "Model name should be cleaned of redundant prefixes")

    @patch('models.get_provider_config')
    def test_metadata_transformation_openai(self, mock_get_config):
        """Test OpenAI transformation (ID 'openai' -> 'OpenAI')"""
        mock_get_config.return_value = {
            "name": "OpenAI",
            "litellm_provider": "openai"
        }

        wrapper = LiteLLMChatWrapper(
            model="gpt-4o",
            provider="openai"
        )
        
        provider_name = wrapper.display_provider
        model_name = wrapper.display_model
        
        self.assertEqual(provider_name, "OpenAI")
        self.assertEqual(model_name, "gpt-4o")

    @patch('models.get_provider_config')
    def test_metadata_transformation_xai(self, mock_get_config):
        """Test xAI transformation (ID 'xai' -> 'xAI')"""
        mock_get_config.return_value = {
            "name": "xAI",
            "litellm_provider": "xai"
        }

        wrapper = LiteLLMChatWrapper(
            model="grok-beta",
            provider="xai"
        )
        
        provider_name = wrapper.display_provider
        model_name = wrapper.display_model
        
        self.assertEqual(provider_name, "xAI")
        # xai/grok-beta -> grok-beta
        self.assertEqual(model_name, "grok-beta")

if __name__ == "__main__":
    # Note: Running this locally might fail if dependencies are missing,
    # but the logic check remains valid for Docker verification.
    unittest.main()
