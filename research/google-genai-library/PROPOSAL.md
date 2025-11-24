# Proposal: Migrate to google-genai Library

## Recommendation
**Migrate from requests-based REST API to google-genai library (version 1.52.0)**

## Justification

### 1. Code Simplification (Primary)
Migration reduces code complexity by 83% (30 lines → 5 lines per request).

**Before (REST API):**
```python
url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"
params = {"key": self.api_key}
headers = {"Content-Type": "application/json"}
payload = {"contents": [{"parts": [{"text": message}]}]}
response = requests.post(url, headers=headers, json=payload, params=params, timeout=30)
if not response.ok:
    raise Exception(f"Gemini API error: {response.status_code} - {response.text}")
data = response.json()
text = data["candidates"][0]["content"]["parts"][0]["text"]
```

**After (google-genai):**
```python
response = self.client.models.generate_content(
    model=model_id,
    contents=message
)
text = response.text
```

### 2. Maintenance & Future-Proofing (Secondary)
- Official Google SDK receives automatic updates
- New features available immediately (streaming, function calling, vision)
- Breaking API changes handled by SDK maintainers
- Type safety via Pydantic models prevents runtime errors

### 3. Developer Experience (Tertiary)
- Simpler API: method call vs manual HTTP construction
- Better error messages: structured exceptions vs HTTP status codes
- Built-in retry logic: automatic exponential backoff
- Type hints: IDE autocomplete and type checking

### 4. Risk Assessment
**Migration Risk:** LOW
- Single dependency addition (`google-genai`)
- Well-defined migration path (see Implementation Plan)
- Backward compatible: can test alongside existing code
- Estimated effort: 1-2 hours

**Long-term Risk:** LOWER than current implementation
- Reduced maintenance burden
- Fewer potential bugs (less custom code)
- Better error handling
- Future API changes handled automatically

## Implementation Plan

### Phase 1: Add Dependency (5 minutes)
```bash
pip install google-genai==1.52.0
```

Update `requirements.txt` or `pyproject.toml`:
```
google-genai==1.52.0
```

### Phase 2: Update GeminiAPI Class (30 minutes)

**File:** `/home/runner/work/NewKiAssist/NewKiAssist/python-lib/kiassist_utils/gemini.py`

**Changes:**
1. Import google-genai: `from google import genai`
2. Initialize client in `__init__`: `self.client = genai.Client(api_key=api_key)`
3. Replace `send_message()` implementation with SDK call
4. Update error handling to use `genai.errors.APIError`

**Code Diff:**
```python
# OLD
import requests
from typing import Dict, Any, Optional

class GeminiAPI:
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    def send_message(self, message: str, model: str = "2.5-flash") -> str:
        model_id = self.MODEL_MAP.get(model, "gemini-1.5-flash")
        url = f"{self.BASE_URL}/{model_id}:generateContent"
        # ... 25+ more lines ...

# NEW
from google import genai
from google.genai import errors
from typing import Optional

class GeminiAPI:
    MODEL_MAP = {
        "2.5-flash": "gemini-1.5-flash",
        "2.5-pro": "gemini-1.5-pro",
        "3-flash": "gemini-1.5-flash",
        "3-pro": "gemini-1.5-pro",
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

### Phase 3: Update Tests (30 minutes)
Update mocks to patch `client.models.generate_content` instead of `requests.post`.

**Before:**
```python
with mock.patch('requests.post') as mock_post:
    mock_post.return_value.ok = True
    mock_post.return_value.json.return_value = {...}
```

**After:**
```python
with mock.patch.object(gemini_api.client.models, 'generate_content') as mock_gen:
    mock_gen.return_value.text = "Response text"
```

### Phase 4: Integration Testing (30 minutes)
1. Test with real API key in development environment
2. Verify gemini-1.5-flash model works
3. Verify gemini-1.5-pro model works
4. Test error handling scenarios
5. Verify timeout behavior

### Phase 5: Documentation Update (15 minutes)
Update any documentation referencing the implementation:
- Update architecture diagrams if applicable
- Update API integration documentation
- Add notes about SDK version dependency

## Rollback Plan
If issues arise, rollback is simple:
1. Remove `google-genai` from dependencies
2. Restore previous `gemini.py` from version control
3. No data migration needed
4. No external state changes

## Success Criteria
- [ ] All existing tests pass
- [ ] New unit tests pass with mocked SDK calls
- [ ] Integration tests pass with real API
- [ ] Code review approved
- [ ] Documentation updated
- [ ] No performance degradation (response time within 5%)

## Alternative Considered: Keep REST API
**Rejected because:**
- Higher maintenance burden (30 lines vs 5 lines)
- Manual error handling prone to bugs
- Missing features (streaming, async, function calling)
- No type safety
- Breaking API changes require manual fixes

**Only valid if:**
- Google-genai library has blocking bugs (none found in 1.52.0)
- Extreme dependency minimization required (not applicable)
- Need features unavailable in SDK (none identified)

## Timeline
**Total Estimated Time:** 1-2 hours
- Phase 1: 5 minutes
- Phase 2: 30 minutes
- Phase 3: 30 minutes  
- Phase 4: 30 minutes
- Phase 5: 15 minutes
- Buffer: 15 minutes

**Recommended Start:** Immediate - no blocking dependencies
