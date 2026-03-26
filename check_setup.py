"""
Run this first: python check_setup.py
It will tell you exactly what is wrong before you start the app.
"""
import os, sys

print("=" * 55)
print("  QPGen Setup Checker")
print("=" * 55)

# 1. .env file
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
print(f"\n[1] .env file at: {env_path}")
if os.path.exists(env_path):
    print("    ✓ .env file EXISTS")
    from dotenv import load_dotenv
    load_dotenv(env_path, override=True)
else:
    print("    ✗ .env file MISSING")
    print("    FIX: Create a file named '.env' in this folder with:")
    print("         GEMINI_API_KEY=AIza...your_key_here")
    sys.exit(1)

# 2. API key
key = os.environ.get('GEMINI_API_KEY', '').strip()
print(f"\n[2] GEMINI_API_KEY in .env:")
if not key:
    print("    ✗ Key is EMPTY or not set")
    print("    FIX: Edit .env and add: GEMINI_API_KEY=AIza...your_actual_key")
    sys.exit(1)
elif not key.startswith('AI'):
    print(f"    ⚠  Key found but unusual prefix: {key[:6]}...")
    print("    Make sure you copied the full key from Google AI Studio")
else:
    print(f"    ✓ Key found: {key[:8]}...{key[-4:]} (length: {len(key)})")

# 3. google-genai SDK
print(f"\n[3] google-genai SDK:")
try:
    from google import genai
    print(f"    ✓ Installed (version: {genai.__version__ if hasattr(genai,'__version__') else 'unknown'})")
except ImportError:
    print("    ✗ NOT installed")
    print("    FIX: pip install google-genai")
    sys.exit(1)

# 4. Live API test
print(f"\n[4] Live Gemini API test:")
try:
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model='gemini-2.0-flash',
        contents='Say exactly: CONNECTED'
    )
    print(f"    ✓ API WORKS! Response: {resp.text.strip()[:50]}")
except Exception as e:
    print(f"    ✗ API FAILED: {type(e).__name__}: {e}")
    print("    Common causes:")
    print("    - Wrong API key → get one at https://aistudio.google.com/apikey")
    print("    - Network/proxy issue blocking Gemini endpoints")
    sys.exit(1)

print("\n" + "=" * 55)
print("  ✓ All checks passed! Run: python app.py")
print("=" * 55 + "\n")
