import os, httpx
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("SPRINGER_API_KEY")
assert key, "SPRINGER_API_KEY 未设置"

# 用开放获取 API
url = "https://api.springernature.com/openaccess/json"
params = {"q": "tetracycline", "p": "1", "api_key": key}

r = httpx.get(url, params=params, timeout=20)
print(r.status_code)
print(r.text[:500])
