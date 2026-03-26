from dotenv import load_dotenv
import os

load_dotenv()
key = os.environ.get('GEMINI_API_KEY', '').strip()

from google import genai
client = genai.Client(api_key=key)

print("Testing different model formats...")

# Test models that should work with new SDK
test_models = [
    'gemini-2.0-flash',
    'models/gemini-2.0-flash',
    'gemini-1.5-flash',
    'models/gemini-1.5-flash',
]

for model in test_models:
    try:
        print(f"\nTesting: {model}")
        resp = client.models.generate_content(
            model=model,
            contents='Say: TEST'
        )
        print(f"✅ SUCCESS: {model} -> {resp.text.strip()}")
        break
    except Exception as e:
        print(f"❌ FAILED: {model} -> {type(e).__name__}")
