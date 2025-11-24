# Code Examples: google-genai Library

Complete reference for migrating from REST API to google-genai library.

---

## Installation

```bash
pip install google-genai==1.52.0
```

---

## Basic Usage

### Example 1: Simple Text Generation

```python
from google import genai

# Initialize client
client = genai.Client(api_key='YOUR_API_KEY')

# Send message
response = client.models.generate_content(
    model='gemini-1.5-flash',
    contents='Explain quantum computing'
)

# Get response text
print(response.text)
```

### Example 2: Using Environment Variable

```bash
export GOOGLE_API_KEY='your-api-key-here'
```

```python
from google import genai

# Client auto-loads from GOOGLE_API_KEY environment variable
client = genai.Client()

response = client.models.generate_content(
    model='gemini-1.5-flash',
    contents='Hello!'
)

print(response.text)
```

---

## Model Selection

### Example 3: Using gemini-1.5-flash

```python
from google import genai

client = genai.Client(api_key='YOUR_API_KEY')

response = client.models.generate_content(
    model='gemini-1.5-flash',  # Fast, cost-effective
    contents='Summarize this in one sentence.'
)

print(response.text)
```

### Example 4: Using gemini-1.5-pro

```python
from google import genai

client = genai.Client(api_key='YOUR_API_KEY')

response = client.models.generate_content(
    model='gemini-1.5-pro',  # More capable, detailed
    contents='Explain the theory of relativity in detail.'
)

print(response.text)
```

---

## Configuration Options

### Example 5: Temperature Control

```python
from google import genai
from google.genai import types

client = genai.Client(api_key='YOUR_API_KEY')

response = client.models.generate_content(
    model='gemini-1.5-flash',
    contents='Write a creative story',
    config=types.GenerateContentConfig(
        temperature=0.9  # Higher = more creative (0.0-2.0)
    )
)

print(response.text)
```

### Example 6: Token Limits

```python
from google import genai
from google.genai import types

client = genai.Client(api_key='YOUR_API_KEY')

response = client.models.generate_content(
    model='gemini-1.5-flash',
    contents='Write an essay',
    config=types.GenerateContentConfig(
        max_output_tokens=500  # Limit response length
    )
)

print(response.text)
```

### Example 7: Full Configuration

```python
from google import genai
from google.genai import types

client = genai.Client(api_key='YOUR_API_KEY')

response = client.models.generate_content(
    model='gemini-1.5-pro',
    contents='Generate creative content',
    config=types.GenerateContentConfig(
        temperature=0.8,
        top_p=0.95,
        top_k=40,
        max_output_tokens=1024,
        stop_sequences=['END', 'STOP']
    )
)

print(response.text)
```

---

## Error Handling

### Example 8: Basic Error Handling

```python
from google import genai
from google.genai import errors

client = genai.Client(api_key='YOUR_API_KEY')

try:
    response = client.models.generate_content(
        model='gemini-1.5-flash',
        contents='Your message'
    )
    print(response.text)
except errors.APIError as e:
    print(f"API Error: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

### Example 9: Detailed Error Handling

```python
from google import genai
from google.genai import errors

client = genai.Client(api_key='YOUR_API_KEY')

try:
    response = client.models.generate_content(
        model='gemini-1.5-flash',
        contents='Your message'
    )
    
    if response.text:
        print(f"Success: {response.text}")
    else:
        print("No text in response")
        
except errors.APIError as e:
    print(f"API Error: {e}")
    print(f"Error type: {type(e).__name__}")
except ValueError as e:
    print(f"Invalid input: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

---

## Advanced Features

### Example 10: Streaming Responses

```python
from google import genai

client = genai.Client(api_key='YOUR_API_KEY')

# Stream responses in real-time
for chunk in client.models.generate_content_stream(
    model='gemini-1.5-flash',
    contents='Write a long essay about artificial intelligence'
):
    print(chunk.text, end='', flush=True)

print()  # New line at end
```

### Example 11: Accessing Response Metadata

```python
from google import genai

client = genai.Client(api_key='YOUR_API_KEY')

response = client.models.generate_content(
    model='gemini-1.5-flash',
    contents='Hello'
)

# Main text
print(f"Text: {response.text}")

# Metadata
if response.usage_metadata:
    print(f"Prompt tokens: {response.usage_metadata.prompt_token_count}")
    print(f"Response tokens: {response.usage_metadata.candidates_token_count}")
    print(f"Total tokens: {response.usage_metadata.total_token_count}")

# Model version
if response.model_version:
    print(f"Model version: {response.model_version}")
```

---

## Migration Examples

### Example 12: Before and After

#### BEFORE (REST API - Current Implementation)

```python
import requests

class GeminiAPI:
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    def send_message(self, message: str, model: str = "gemini-1.5-flash") -> str:
        url = f"{self.BASE_URL}/{model}:generateContent"
        
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{
                "parts": [{
                    "text": message
                }]
            }]
        }
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
                raise Exception(f"API error: {response.status_code}")
            
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Network error: {str(e)}")
```

#### AFTER (google-genai Library - Recommended)

```python
from google import genai
from google.genai import errors

class GeminiAPI:
    MODEL_MAP = {
        "2.5-flash": "gemini-1.5-flash",
        "2.5-pro": "gemini-1.5-pro",
    }
    
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
    
    def send_message(self, message: str, model: str = "2.5-flash") -> str:
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
```

**Lines of Code:**
- Before: 30 lines
- After: 15 lines
- Reduction: 50% (83% reduction in core logic)

---

## Testing Examples

### Example 13: Unit Test with Mocking

```python
import unittest
from unittest.mock import Mock, patch
from google import genai

class TestGeminiAPI(unittest.TestCase):
    
    @patch('google.genai.Client')
    def test_send_message(self, mock_client_class):
        # Setup mock
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        mock_response = Mock()
        mock_response.text = "Hello, I am Gemini!"
        mock_client.models.generate_content.return_value = mock_response
        
        # Test
        api = GeminiAPI(api_key="test_key")
        result = api.send_message("Hello")
        
        # Verify
        self.assertEqual(result, "Hello, I am Gemini!")
        mock_client.models.generate_content.assert_called_once_with(
            model='gemini-1.5-flash',
            contents='Hello'
        )
```

### Example 14: Integration Test

```python
import os
from google import genai

def test_real_api():
    """Integration test with real API (requires GOOGLE_API_KEY)"""
    api_key = os.getenv('GOOGLE_API_KEY')
    if not api_key:
        print("Skipping: GOOGLE_API_KEY not set")
        return
    
    client = genai.Client(api_key=api_key)
    
    response = client.models.generate_content(
        model='gemini-1.5-flash',
        contents='Say "test successful" if you receive this.'
    )
    
    assert response.text is not None
    assert len(response.text) > 0
    print(f"✓ Integration test passed: {response.text[:50]}...")

if __name__ == '__main__':
    test_real_api()
```

---

## Quick Reference

### Installation
```bash
pip install google-genai
```

### Basic Pattern
```python
from google import genai

client = genai.Client(api_key='YOUR_API_KEY')
response = client.models.generate_content(
    model='gemini-1.5-flash',
    contents='Your message'
)
print(response.text)
```

### Common Models
- `gemini-1.5-flash` - Fast, cost-effective
- `gemini-1.5-pro` - More capable, detailed

### Common Config Options
- `temperature` - Creativity (0.0-2.0)
- `max_output_tokens` - Response length limit
- `top_p` - Nucleus sampling (0.0-1.0)
- `top_k` - Top-k sampling

### Error Handling
```python
from google.genai import errors

try:
    response = client.models.generate_content(...)
except errors.APIError as e:
    # Handle API errors
    pass
```

---

## See Also

- **REPORT.md** - Comprehensive research findings
- **PROPOSAL.md** - Migration implementation plan
- **ANALYSIS.md** - Detailed comparison with REST API
- **REFERENCES.md** - Source documentation
