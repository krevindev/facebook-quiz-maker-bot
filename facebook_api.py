import requests
from config import PAGE_ACCESS_TOKEN

def send_message(recipient_id, text):
    print(f"Sending to {recipient_id}: {text}")
    url = f"https://graph.facebook.com/v17.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    r = requests.post(url, json=payload)
    if r.status_code != 200:
        print(f"FB send error: {r.status_code} {r.text}")

def send_quick_replies(recipient_id, text, replies):
    url = f"https://graph.facebook.com/v17.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    quick_replies = [{"content_type": "text", "title": r, "payload": r} for r in replies]
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text, "quick_replies": quick_replies}
    }
    r = requests.post(url, json=payload)
    if r.status_code != 200:
        print(f"FB quick reply send error: {r.status_code} {r.text}")

def send_menu(recipient_id):
    send_quick_replies(
        recipient_id,
        "📋 Main Menu:\nChoose an option:",
        ["1️⃣ Upload a file for quiz", "2️⃣ Topic-based advanced medical quiz", "3️⃣ Random medical quiz"]
    )
