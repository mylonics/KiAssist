"""Gemini API integration module."""

import requests
from typing import Dict, Any, Optional


class GeminiAPI:
    """Handle interactions with Google Gemini API."""
    
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
    
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
        self.api_key = api_key
    
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
        
        url = f"{self.BASE_URL}/{model_id}:generateContent"
        
        headers = {
            "Content-Type": "application/json"
        }
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": message
                        }
                    ]
                }
            ]
        }
        
        # Add API key as query parameter
        params = {"key": self.api_key}
        
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                params=params,
                timeout=30
            )
            
            if not response.ok:
                error_msg = f"Gemini API error: {response.status_code} - {response.text}"
                raise Exception(error_msg)
            
            data = response.json()
            
            # Extract response text
            if "candidates" in data and len(data["candidates"]) > 0:
                candidate = data["candidates"][0]
                if "content" in candidate and "parts" in candidate["content"]:
                    parts = candidate["content"]["parts"]
                    if len(parts) > 0 and "text" in parts[0]:
                        return parts[0]["text"]
            
            raise Exception("No response from Gemini API")
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Network error: {str(e)}")
