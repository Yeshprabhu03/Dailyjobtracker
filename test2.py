import requests, os
from dotenv import load_dotenv
load_dotenv()
key = os.getenv("GEMINI_API_KEY")
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
r = requests.get(url)
if r.status_code == 200:
    models = r.json().get('models', [])
    names = [m['name'] for m in models]
    print(f"Available models: {', '.join(names)}")
else:
    print(f"Error {r.status_code}: {r.text[:500]}")
