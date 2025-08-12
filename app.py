import os
import json
import requests
import re
from flask import Flask, request
from PyPDF2 import PdfReader

PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "")
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = "mistralai/mixtral-8x7b-instruct"

app = Flask(__name__)

# --- Menu State ---
user_state = {}  # {user_id: {"mode": "menu"|"quiz", "questions": [...], "current": 0}}

# --- Facebook Send ---
def send_message(recipient_id, text, quick_replies=None):
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    if quick_replies:
        payload["message"]["quick_replies"] = quick_replies
    resp = requests.post(
        f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}",
        json=payload
    )
    if resp.status_code != 200:
        print("Send error:", resp.status_code, resp.text)

# --- Menu ---
def show_main_menu(user_id):
    send_message(
        user_id,
        "📋 Main Menu - Choose an option:",
        quick_replies=[
            {"content_type": "text", "title": "📂 Upload File Topic", "payload": "UPLOAD_FILE"},
            {"content_type": "text", "title": "🎯 Random Nursing Quiz", "payload": "RANDOM_QUIZ"}
        ]
    )
    user_state[user_id] = {"mode": "menu"}

# --- Question Formatting ---
def format_question(q):
    opts = q["options"]
    return f"{q['question']}\n\n" + "\n".join([f"{chr(65+i)}) {opt}" for i, opt in enumerate(opts)])

def send_question(user_id):
    state = user_state[user_id]
    q = state["questions"][state["current"]]
    quick_replies = [{"content_type": "text", "title": chr(65+i), "payload": chr(65+i)} for i in range(len(q["options"]))]
    send_message(user_id, format_question(q), quick_replies=quick_replies)

# --- OpenRouter Quiz Generation ---
def generate_quiz_from_text(text, num_questions=5):
    prompt = (
        f"Generate {num_questions} multiple-choice questions on Advanced Nursing based ONLY on this text:\n"
        f"{text}\n\n"
        "Return JSON in format: [{\"question\":..., \"options\": [..], \"answer\": \"A\"}]."
    )
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}]
            }
        )
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        quiz = json.loads(re.search(r"\[.*\]", content, re.S).group(0))
        return quiz
    except Exception as e:
        print("OpenRouter failed:", e)
        return fallback_questions(num_questions)

# --- Fallback ---
def fallback_questions(n=5):
    questions = [
        {
            "question": "Nursing interventions for COPD include administering bronchodilators and encouraging deep breathing exercises.",
            "options": ["COPD", "Bronchiectasis", "Asthma", "Lung cancer"],
            "answer": "A"
        },
        {
            "question": "Which electrolyte imbalance is most likely with diuretic use?",
            "options": ["Hyperkalemia", "Hypokalemia", "Hypernatremia", "Hyponatremia"],
            "answer": "B"
        },
        {
            "question": "What position improves oxygenation in acute respiratory distress?",
            "options": ["Prone", "Supine", "High Fowler's", "Trendelenburg"],
            "answer": "A"
        },
        {
            "question": "Which is a priority nursing diagnosis for a client with pneumonia?",
            "options": ["Risk for falls", "Impaired gas exchange", "Imbalanced nutrition", "Chronic pain"],
            "answer": "B"
        },
        {
            "question": "Which oxygen delivery method provides the highest concentration?",
            "options": ["Nasal cannula", "Simple mask", "Non-rebreather mask", "Venturi mask"],
            "answer": "C"
        }
    ]
    return questions[:n]

# --- PDF Extraction ---
def extract_text_from_pdf(url):
    try:
        r = requests.get(url)
        with open("temp.pdf", "wb") as f:
            f.write(r.content)
        reader = PdfReader("temp.pdf")
        return "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
    except Exception as e:
        print("PDF extraction failed:", e)
        return ""

# --- Webhook ---
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Invalid verification token"

    data = request.get_json()
    print("Webhook data:", data)

    if "entry" in data:
        for entry in data["entry"]:
            for msg_event in entry.get("messaging", []):
                sender_id = msg_event["sender"]["id"]

                if "message" in msg_event:
                    if "quick_reply" in msg_event["message"]:
                        payload = msg_event["message"]["quick_reply"]["payload"]
                        handle_quick_reply(sender_id, payload)
                    elif "attachments" in msg_event["message"]:
                        for att in msg_event["message"]["attachments"]:
                            if att["type"] == "file":
                                file_url = att["payload"]["url"]
                                text = extract_text_from_pdf(file_url)
                                questions = generate_quiz_from_text(text, num_questions=7)
                                user_state[sender_id] = {"mode": "quiz", "questions": questions, "current": 0}
                                send_question(sender_id)
                    elif "text" in msg_event["message"]:
                        handle_text(sender_id, msg_event["message"]["text"])

    return "ok"

def handle_quick_reply(user_id, payload):
    state = user_state.get(user_id, {"mode": "menu"})
    if payload == "UPLOAD_FILE":
        send_message(user_id, "📄 Please upload your PDF file to start the quiz.")
        state["mode"] = "await_file"
        user_state[user_id] = state
    elif payload == "RANDOM_QUIZ":
        questions = generate_quiz_from_text("Advanced Nursing concepts", num_questions=7)
        user_state[user_id] = {"mode": "quiz", "questions": questions, "current": 0}
        send_question(user_id)
    elif state.get("mode") == "quiz":
        current_q = state["questions"][state["current"]]
        correct_letter = current_q["answer"].strip().upper()
        if payload.strip().upper() == correct_letter:
            send_message(user_id, "✅ Correct!")
        else:
            send_message(user_id, f"❌ Incorrect. Correct: {correct_letter}) {current_q['options'][ord(correct_letter)-65]}")
        state["current"] += 1
        if state["current"] < len(state["questions"]):
            send_question(user_id)
        else:
            send_message(user_id, "🎉 Quiz complete!")
            show_main_menu(user_id)

def handle_text(user_id, text):
    state = user_state.get(user_id, {"mode": "menu"})
    if state["mode"] == "menu":
        show_main_menu(user_id)
    elif state["mode"] == "quiz":
        handle_quick_reply(user_id, text)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
