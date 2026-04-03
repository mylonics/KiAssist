"""Gemini API integration module."""

from google import genai
from google.genai import errors
from typing import Optional


class GeminiAPI:
    """Handle interactions with Google Gemini API."""
    
    # Model mapping from UI selection to actual Gemini model IDs
    # These are the model IDs used by the google-genai library
    MODEL_MAP = {
        "3.1-pro": "gemini-3.1-pro-preview",              # Flagship model for complex agentic workflows & coding
        "3-flash": "gemini-3-flash-preview",               # Pro-level intelligence at Flash speeds
        "3.1-flash-lite": "gemini-3.1-flash-lite-preview",  # Ultra-low latency, high-volume workhorse
    }
    
    def __init__(self, api_key: str):
        """Initialize Gemini API client.
        
        Args:
            api_key: Google Gemini API key
        """
        self.client = genai.Client(api_key=api_key)
    
    def send_message(self, message: str, model: str = "3-flash") -> str:
        """Send a message to Gemini and get a response.
        
        Args:
            message: The message to send
            model: The model identifier (3-flash, 3.1-pro, etc.)
            
        Returns:
            The response text from Gemini
            
        Raises:
            Exception: If the API call fails
        """
        # Map model name to full Gemini model ID
        model_id = self.MODEL_MAP.get(model, "gemini-3-flash-preview")
        
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
