import requests
from config import PAGE_ACCESS_TOKEN

def send_message(recipient_id, text):
    try:
        print(f"Sending to {recipient_id}: {text}")
        url = f"https://graph.facebook.com/v17.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
        payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
        r = requests.post(url, json=payload)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"FB send_message error: {e}")

def send_quick_replies(recipient_id, text, replies):
    try:
        url = f"https://graph.facebook.com/v17.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
        quick_replies = [{"content_type": "text", "title": r, "payload": r} for r in replies]
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text, "quick_replies": quick_replies}
        }
        r = requests.post(url, json=payload)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"FB send_quick_replies error: {e}")

def send_menu(recipient_id):
    try:
        send_quick_replies(
            recipient_id,
            "📋 Main Menu:\nChoose an option:",
            ["1️⃣ Upload a file for quiz", "2️⃣ Enter topic for quiz", "3️⃣ Random quiz"]
        )
    except Exception as e:
        print(f"send_menu error: {e}")
