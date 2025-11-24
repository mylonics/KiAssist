# References

## 1. Package Information
**Source:** PyPI package index via `pip index versions google-genai`  
**Version:** 1.52.0 (latest as of research date)  
**Package Name:** `google-genai`  
**Dependencies:** anyio, google-auth, httpx, pydantic, requests, tenacity, typing-extensions, websockets

## 2. Module Documentation
**Source:** Python introspection via `help(google.genai)`  
**Module Name:** `google.genai`  
**Primary Class:** `google.genai.client.Client`  
**Key Methods:** `client.models.generate_content()`, `client.models.generate_content_stream()`

## 3. Client Class Documentation
**Source:** `help(google.genai.Client)` and `inspect.signature()`  
**Initialization Parameters:**
- `api_key`: Optional[str] - API key for Gemini Developer API
- `vertexai`: Optional[bool] - Use Vertex AI endpoints (default: False)
- `project`: Optional[str] - Google Cloud project ID (Vertex AI only)
- `location`: Optional[str] - API region (Vertex AI only)
- `credentials`: Optional[Credentials] - Auth credentials (Vertex AI only)
- `http_options`: Optional[HttpOptions] - HTTP configuration

**Environment Variables:**
- `GOOGLE_API_KEY` - Auto-loaded if set in environment

## 4. GenerateContent Method Documentation
**Source:** Method docstring via `client.models.generate_content.__doc__`  
**Signature:**
```
generate_content(
    *,
    model: str,
    contents: Union[Content, str, File, Part, list[...]],
    config: Optional[GenerateContentConfig] = None
) -> GenerateContentResponse
```

**Model Format (Gemini API):**
- Model ID: `gemini-1.5-flash`, `gemini-1.5-pro`
- Full name: `models/gemini-1.5-flash`
- Tuned models: `tunedModels/{id}`

## 5. Response Object Structure
**Source:** Type introspection via `__annotations__`  
**Class:** `google.genai.types.GenerateContentResponse`  
**Key Attributes:**
- `text`: str - Main text response (property)
- `candidates`: Optional[list[Candidate]] - Response candidates
- `usage_metadata`: Optional[UsageMetadata] - Token usage statistics
- `prompt_feedback`: Optional[PromptFeedback] - Prompt safety feedback
- `model_version`: Optional[str] - Model version used
- `response_id`: Optional[str] - Response identifier

## 6. Configuration Options
**Source:** Type annotations for `GenerateContentConfig`  
**Key Parameters:**
- `temperature`: float (0.0-2.0) - Controls randomness
- `top_p`: float - Nucleus sampling threshold
- `top_k`: float - Top-k sampling limit
- `max_output_tokens`: int - Maximum response length
- `stop_sequences`: list[str] - Sequences that stop generation
- `safety_settings`: list[SafetySetting] - Content safety filters
- `system_instruction`: str - System-level instructions

## 7. Current Implementation Reference
**Source:** `/home/runner/work/NewKiAssist/NewKiAssist/python-lib/kiassist_utils/gemini.py`  
**Lines:** 1-92  
**Implementation:** Custom GeminiAPI class using requests library  
**URL Pattern:** `{BASE_URL}/{model_id}:generateContent?key={api_key}`
