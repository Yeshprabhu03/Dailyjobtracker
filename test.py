import requests, os
from dotenv import load_dotenv
load_dotenv()
key = os.getenv("GEMINI_API_KEY")
url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
r = requests.post(url, json={"contents": [{"parts": [{"text": "Reply with 'API works!'"}]}]}, headers={"Content-Type": "application/json"})
print(f"Status Code: {r.status_code}")
print(f"Response: {r.text[:500]}")
