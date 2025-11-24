"""Gemini API integration module."""

from google import genai
from google.genai import errors
from typing import Optional


class GeminiAPI:
    """Handle interactions with Google Gemini API."""
    
    # Model mapping
    MODEL_MAP = {
        "2.5-flash": "gemini-1.5-flash",
        "2.5-pro": "gemini-1.5-pro",
        "3-flash": "gemini-1.5-flash",  # Using 1.5-flash for now
        "3-pro": "gemini-1.5-pro",      # Using 1.5-pro for now
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
        model_id = self.MODEL_MAP.get(model, "gemini-1.5-flash")
        
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
