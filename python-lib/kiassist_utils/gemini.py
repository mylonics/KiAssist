"""Gemini API integration module."""

from google import genai
from google.genai import errors
from typing import Optional


class GeminiAPI:
    """Handle interactions with Google Gemini API."""
    
    # Model mapping from UI selection to actual Gemini model IDs
    # These are the model IDs used by the google-genai library
    MODEL_MAP = {
        "2.5-flash": "gemini-2.5-flash",      # Use Gemini 2.5 Flash (latest stable)
        "2.5-pro": "gemini-2.5-pro",          # Gemini 2.5 Pro
        "3-flash": "gemini-3.0-flash",        # Use Gemini 3.0 Flash
        "3-pro": "gemini-3.0-pro",            # Gemini 3.0 Pro
    }
    
    def __init__(self, api_key: str):
        """Initialize Gemini API client.
        
        Args:
            api_key: Google Gemini API key
        """
        self.client = genai.Client(api_key=api_key)
    
    def send_message(self, message: str, model: str = "2.5-flash") -> str:
        """Send a message to Gemini and get a response.
        
        Args:
            message: The message to send
            model: The model identifier (2.5-flash, 2.5-pro, etc.)
            
        Returns:
            The response text from Gemini
            
        Raises:
            Exception: If the API call fails
        """
        # Map model name to full Gemini model ID
        model_id = self.MODEL_MAP.get(model, "gemini-2.0-flash")
        
        try:
            response = self.client.models.generate_content(
                model=model_id,
                contents=message
            )
            return response.text
            
        except errors.APIError as e:
            raise Exception(f"Gemini API error: {str(e)}")
        except Exception as e:
            raise Exception(f"Unexpected error: {str(e)}")
