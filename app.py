import os
import re
import requests
from flask import Flask, request
from PyPDF2 import PdfReader
import docx
from io import BytesIO

# --- CONFIG ---
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN", "verify_token")
PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = "mistralai/mixtral-8x7b-instruct"

app = Flask(__name__)

# In-memory session store (stateless-ish)
user_sessions = {}

# --- Utilities ---
def clean_text(text):
    text = re.sub(r'\s+', ' ', text)  
    text = re.sub(r'/[A-Za-z0-9]+', '', text)  
    text = re.sub(r'[^\x20-\x7E]+', ' ', text)  
    text = re.sub(r'\b(?:BT|ET|Tf|Td|Tj|EMC)\b', '', text)  
    lines = [line for line in text.splitlines() if re.search(r'[A-Za-z]', line)]
    return ' '.join(lines).strip()

# --- FB Send Functions ---
def send_message(recipient_id, text):
    print(f"Sending to {recipient_id}: {text}")
    url = f"https://graph.facebook.com/v17.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    r = requests.post(url, json=payload)
    if r.status_code != 200:
        print(f"FB send error: {r.status_code} {r.text}")

def send_quick_replies(recipient_id, text, replies):
    """Send Facebook Quick Replies"""
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
    user_sessions[recipient_id] = {"state": "awaiting_menu"}

# --- File Processing ---
def extract_text_from_url(file_url):
    try:
        resp = requests.get(file_url)
        resp.raise_for_status()
        content = resp.content
        if file_url.lower().endswith(".pdf"):
            pdf = PdfReader(BytesIO(content))
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
        elif file_url.lower().endswith((".docx", ".doc")):
            doc = docx.Document(BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs)
        else:
            return content.decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"File extract error: {e}")
        return ""

# --- LLM ---
def generate_quiz_from_text(text, num_q=5):
    prompt = (
        f"Generate {num_q} multiple-choice questions (A-D) from the text below. "
        f"Questions must be about advanced nursing topics and lessons only from the text.\n\n"
        f"Strict format:\n"
        f"Question?\nA) ...\nB) ...\nC) ...\nD) ...\nAnswer: <LETTER>\n\n"
        f"Text:\n{text[:3000]}"
    )
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://your-app.com",
        "X-Title": "FB Quiz Bot",
    }
    data = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
    if r.status_code != 200:
        print(f"LLM error: {r.status_code} {r.text}")
        return []
    text = r.json()["choices"][0]["message"]["content"]
    return parse_questions(text)

def parse_questions(raw):
    blocks = re.split(r"\n(?=\d+\)|Question)", raw)
    questions = []
    for block in blocks:
        q_match = re.search(r"^(.*?\?)\s*A\)", block, re.S | re.M)
        if not q_match:
            continue
        question = q_match.group(1).strip()
        opts = re.findall(r"([A-D])\)\s*(.+)", block)
        ans_match = re.search(r"Answer:\s*([A-D])", block, re.I)
        answer = ans_match.group(1).upper() if ans_match else None
        if len(opts) == 4 and answer:
            questions.append({
                "question": question,
                "options": {opt[0]: opt[1] for opt in opts},
                "answer": answer
            })
    return questions

# --- Quiz Logic ---
def start_quiz(recipient_id, questions):
    if not questions:
        send_message(recipient_id, "No quiz could be generated. Please try again.")
        send_menu(recipient_id)
        return
    user_sessions[recipient_id] = {"state": "in_quiz", "questions": questions, "index": 0, "score": 0}
    ask_question(recipient_id)

def ask_question(recipient_id):
    sess = user_sessions[recipient_id]
    idx = sess["index"]
    if idx >= len(sess["questions"]):
        send_message(recipient_id, f"✅ Quiz finished! Score: {sess['score']}/{len(sess['questions'])}")
        send_menu(recipient_id)
        return
    
    q = sess["questions"][idx]
    # Build the message with question + choices
    question_text = (
        f"{q['question']}\n"
        f"A) {q['options']['A']}\n"
        f"B) {q['options']['B']}\n"
        f"C) {q['options']['C']}\n"
        f"D) {q['options']['D']}"
    )
    
    # Send question with quick replies A-D
    send_quick_replies(
        recipient_id,
        question_text,
        ["A", "B", "C", "D"]
    )

def handle_answer(recipient_id, text):
    sess = user_sessions.get(recipient_id)
    if not sess or sess.get("state") != "in_quiz":
        send_menu(recipient_id)
        return
    q = sess["questions"][sess["index"]]
    if text.strip().upper().startswith(q["answer"]):
        send_message(recipient_id, "✅ Correct!")
        sess["score"] += 1
    else:
        send_message(recipient_id, f"❌ Incorrect. Correct: {q['answer']}) {q['options'][q['answer']]}")
    sess["index"] += 1
    ask_question(recipient_id)

# --- Webhook ---
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Invalid token", 403

    data = request.json
    print(f"Webhook data: {data}")
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = event["sender"]["id"]
            if "message" in event:
                if "attachments" in event["message"]:
                    for att in event["message"]["attachments"]:
                        if att["type"] == "file":
                            file_url = att["payload"]["url"]
                            text = extract_text_from_url(file_url)

                            if not text.strip():
                                send_message(sender_id, "❌ Could not extract text. Please try another file.")
                                send_menu(sender_id)
                                return "ok", 200

                            cleaned_text = clean_text(text)
                            if len(cleaned_text.split()) < 50:
                                send_message(sender_id, "⚠️ Not enough readable text. Using Advanced Medical fallback.")
                                cleaned_text = "Advanced Nursing concepts"

                            questions = generate_quiz_from_text(cleaned_text, num_q=7)
                            start_quiz(sender_id, questions)
                            return "ok", 200

                elif "text" in event["message"]:
                    handle_text(sender_id, event["message"]["text"])
    return "ok", 200

def handle_text(sender_id, text):
    sess = user_sessions.get(sender_id, {"state": "awaiting_menu"})
    if sess["state"] == "awaiting_menu":
        if text.startswith("1"):
            send_message(sender_id, "📄 Please upload your file now.")
            user_sessions[sender_id] = {"state": "awaiting_file"}
        elif text.startswith("2"):
            send_message(sender_id, "📝 Enter a medical topic:")
            user_sessions[sender_id] = {"state": "awaiting_topic"}
        elif text.startswith("3"):
            questions = generate_quiz_from_text("Advanced Nursing concepts", num_q=7)
            start_quiz(sender_id, questions)
        else:
            send_menu(sender_id)
    elif sess["state"] == "awaiting_topic":
        questions = generate_quiz_from_text(text, num_q=7)
        start_quiz(sender_id, questions)
    elif sess["state"] == "in_quiz":
        handle_answer(sender_id, text)
    elif sess["state"] == "awaiting_file":
        send_message(sender_id, "📄 Please send a file, not text.")
    else:
        send_menu(sender_id)
        
        
def setup_get_started_button():
    url = f"https://graph.facebook.com/v17.0/me/messenger_profile?access_token={PAGE_ACCESS_TOKEN}"
    payload = {
        "get_started": {"payload": "GET_STARTED"}
    }
    r = requests.post(url, json=payload)
    if r.status_code != 200:
        print(f"Error setting Get Started: {r.status_code} {r.text}")
    else:
        print("✅ Get Started button set.")

if __name__ == "__main__":
    setup_get_started_button()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
