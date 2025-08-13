import requests
from config import PAGE_ACCESS_TOKEN
from facebook_api import send_menu

def setup_get_started_button():
    url = f"https://graph.facebook.com/v17.0/me/messenger_profile?access_token={PAGE_ACCESS_TOKEN}"
    payload = {"get_started": {"payload": "GET_STARTED"}}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print("✅ Get Started button set.")
    except requests.RequestException as e:
        print(f"Error setting Get Started button: {e}")

def handle_postback(sender_id, payload, send_message_func, session_set_func):
    try:
        if payload == "GET_STARTED":
            send_message_func(sender_id, "Welcome! Let's get started.")
            send_menu(sender_id)
            session_set_func(sender_id, {"state": "awaiting_menu"})
    except Exception as e:
        print(f"handle_postback error: {e}")
