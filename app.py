import os
import requests
from flask import Flask, request
from PyPDF2 import PdfReader
import docx
import openai
from io import BytesIO
import json

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

# Temporary storage for user data
user_data = {}  # { sender_id: {"mode": None, "lesson": ""} }

# ======= Webhook Verification =======
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        else:
            return "Verification token mismatch", 403
    return "Hello world", 200

# ======= Webhook Receiver =======
@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.get_json()
    print("Webhook data:", data)

    if "entry" in data:
        for entry in data["entry"]:
            if "messaging" in entry:
                for event in entry["messaging"]:
                    sender_id = event["sender"]["id"]

                    if "message" in event:
                        if "attachments" in event["message"]:
                            for attachment in event["message"]["attachments"]:
                                if attachment["type"] == "file":
                                    handle_file_message(sender_id, attachment["payload"]["url"])
                        elif "text" in event["message"]:
                            handle_text_message(sender_id, event["message"]["text"].strip())

                    elif "postback" in event:
                        handle_postback(sender_id, event["postback"]["payload"])

    return "EVENT_RECEIVED", 200

# ======= File Handling =======
def handle_file_message(sender_id, file_url):
    mode = user_data.get(sender_id, {}).get("mode")
    if mode == "upload":
        try:
            file_data = requests.get(file_url).content
            text = extract_text(file_url, file_data)
            if text.strip():
                user_data[sender_id]["lesson"] = text
                send_message(sender_id, "✅ File uploaded successfully! Let's start your quiz.")
                send_quiz_question(sender_id, text)
            else:
                send_message(sender_id, "❌ Couldn't extract text from the file.")
                show_menu(sender_id)
        except Exception as e:
            print("Error handling file:", e)
            send_message(sender_id, "❌ Failed to process the file.")
            show_menu(sender_id)
    else:
        send_message(sender_id, "📌 Please choose 'Upload File' from the menu before sending a file.")
        show_menu(sender_id)

def extract_text(file_url, file_data):
    if file_url.endswith(".pdf"):
        reader = PdfReader(BytesIO(file_data))
        return "\n".join([page.extract_text() or "" for page in reader.pages])
    elif file_url.endswith(".docx"):
        doc = docx.Document(BytesIO(file_data))
        return "\n".join([para.text for para in doc.paragraphs])
    else:
        return ""

# ======= Text Message Handling =======
def handle_text_message(sender_id, text):
    mode = user_data.get(sender_id, {}).get("mode")

    if mode == "topic":
        send_message(sender_id, f"🔍 Generating quiz for topic: {text}")
        send_quiz_question(sender_id, text)
    elif mode == "upload" and "lesson" in user_data.get(sender_id, {}):
        send_quiz_question(sender_id, user_data[sender_id]["lesson"])
    else:
        show_menu(sender_id)

# ======= Postback Handling =======
def handle_postback(sender_id, payload):
    if payload == "MENU_UPLOAD":
        user_data[sender_id] = {"mode": "upload", "lesson": ""}
        send_message(sender_id, "📤 Please upload your PDF or DOCX file now.")

    elif payload == "MENU_TOPIC":
        user_data[sender_id] = {"mode": "topic"}
        send_message(sender_id, "✏️ Please enter your quiz topic:")

    elif payload == "MENU_RANDOM":
        user_data[sender_id] = {"mode": "random"}
        send_message(sender_id, "🎯 Generating a random advanced nursing quiz...")
        send_quiz_question(sender_id, "Advanced nursing topics")

    elif payload in ["A", "B", "C", "D"]:
        send_message(sender_id, f"✅ You selected option {payload}")
        show_menu(sender_id)

    else:
        show_menu(sender_id)

# ======= Menu =======
def show_menu(sender_id):
    buttons = [
        {"type": "postback", "title": "📤 Upload File", "payload": "MENU_UPLOAD"},
        {"type": "postback", "title": "📚 Topic Quiz", "payload": "MENU_TOPIC"},
        {"type": "postback", "title": "🎯 Random Quiz", "payload": "MENU_RANDOM"}
    ]
    payload = {
        "recipient": {"id": sender_id},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": "📋 Main Menu - Choose an option:",
                    "buttons": buttons
                }
            }
        }
    }
    call_send_api(payload)

# ======= Quiz Question Generation =======
def send_quiz_question(sender_id, lesson_text):
    try:
        prompt = f"""
        Based on the following lesson text, generate ONE multiple choice question with exactly 4 options.
        Return it in JSON format: {{ "question": "...", "options": ["A. ...", "B. ...", "C. ...", "D. ..."] }}
        
        Lesson text:
        {lesson_text[:2000]}
        """
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        quiz = json.loads(response.choices[0].message["content"])
        send_quiz_with_letter_buttons(sender_id, quiz["question"], quiz["options"])
    except Exception as e:
        print("Error generating quiz:", e)
        send_message(sender_id, "❌ Could not generate quiz question.")
        show_menu(sender_id)

# ======= Send Quiz with Letter Buttons =======
def send_quiz_with_letter_buttons(sender_id, question, options):
    buttons = []
    for opt in options:
        letter = opt.split(".")[0].strip()
        buttons.append({
            "type": "postback",
            "title": letter,
            "payload": letter
        })
    payload = {
        "recipient": {"id": sender_id},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": question,
                    "buttons": buttons
                }
            }
        }
    }
    call_send_api(payload)

# ======= Send Message Helper =======
def send_message(recipient_id, text):
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    call_send_api(payload)

def call_send_api(payload):
    url = f"https://graph.facebook.com/v17.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    response = requests.post(url, json=payload)
    if response.status_code != 200:
        print("Sent message:", response.status_code, response.text)

# ======= Privacy Policy =======
@app.route("/privacy-policy", methods=["GET"])
def privacy_policy():
    return "This is the privacy policy for our Facebook Messenger bot.", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
