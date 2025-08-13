import os

VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN", "verify_token")
PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = "mistralai/mixtral-8x7b-instruct"
