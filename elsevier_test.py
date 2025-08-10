import os, httpx
from dotenv import load_dotenv; load_dotenv()
key=os.getenv("SCOPUS_API_KEY"); assert key
r=httpx.get(
  "https://api.elsevier.com/content/search/scopus",
  params={"query":"tetracycline","count":"1"},
  headers={"X-ELS-APIKey":key,"Accept":"application/json"}, timeout=20)
print(r.status_code, r.text[:400])
