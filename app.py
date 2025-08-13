import os
import re
import requests
from flask import Flask, request
from io import BytesIO

import pdfplumber
import docx

# --- CONFIG ---
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN", "verify_token")
PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = "mistralai/mixtral-8x7b-instruct"

app = Flask(__name__)

# In-memory session store
user_sessions = {}

# --- Default Knowledge Base for Random Quiz ---
DEFAULT_KB = """
Physics: Newton's laws, energy, and motion.
Biology: Cells, photosynthesis, human anatomy basics.
History: Ancient civilizations, world wars, key events.
Math: Algebra, geometry, probability, fractions.
"""

# --- Utilities ---
def clean_text(text):
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def preprocess_for_quiz(text):
    """Remove headers, course codes, checkmarks, and non-lesson content."""
    lines = text.splitlines()
    content_lines = []
    for line in lines:
        line = line.strip()
        # Skip empty lines, course codes, numbers at the start, or checklist symbols
        if not line or re.match(r'^(NCMB|BACHELOR|COURSE|✓|[0-9]+)', line):
            continue
        content_lines.append(line)
    return " ".join(content_lines)

# --- FB Send Functions ---
def send_message(recipient_id, text):
    url = f"https://graph.facebook.com/v17.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"FB send error: {r.status_code} {r.text}")
    except requests.RequestException as e:
        print(f"FB send exception: {e}")

def send_quick_replies(recipient_id, text, replies):
    url = f"https://graph.facebook.com/v17.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    quick_replies = [{"content_type": "text", "title": r, "payload": r} for r in replies]
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text, "quick_replies": quick_replies}
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"FB quick reply send error: {r.status_code} {r.text}")
    except requests.RequestException as e:
        print(f"FB quick reply exception: {e}")

def send_menu(recipient_id):
    send_quick_replies(
        recipient_id,
        "📋 Main Menu:\nChoose an option:",
        ["1️⃣ Upload a file for quiz", "2️⃣ Topic-based quiz", "3️⃣ Random quiz"]
    )
    user_sessions[recipient_id] = {"state": "awaiting_menu"}

# --- File Processing ---
def extract_pdf_text_only(file_path):
    """Extract visible text only from a text-based PDF (local or URL)."""
    try:
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                lines = page_text.splitlines()
                for line in lines:
                    # Remove lines starting with PDF operators/metadata
                    if re.match(r'^\s*(%|/|BT|ET|Tf|Td|Tj|EMC)', line):
                        continue
                    # Keep lines with actual letters/numbers
                    if re.search(r'[A-Za-z0-9]', line):
                        text += line + "\n"
        return text.strip()
    except Exception as e:
        print(f"PDF extract error: {e}")
        return ""

def extract_text_from_url(file_url):
    try:
        if file_url.lower().endswith(".pdf"):
            resp = requests.get(file_url, timeout=20)
            resp.raise_for_status()
            return extract_pdf_text_only(BytesIO(resp.content))
        elif file_url.lower().endswith((".docx", ".doc")):
            resp = requests.get(file_url, timeout=20)
            resp.raise_for_status()
            doc = docx.Document(BytesIO(resp.content))
            return "\n".join(p.text for p in doc.paragraphs)
        else:
            resp = requests.get(file_url, timeout=20)
            resp.raise_for_status()
            return resp.content.decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"File extract error: {e}")
        return ""

# --- LLM ---
def ai_generate_quiz(text, num_q=5):
    prompt = (
        f"Generate {num_q} multiple-choice questions (A-D) from the following text. "
        f"Questions must focus on the main topics and lessons from the text.\n\n"
        f"Format strictly as:\nQuestion?\nA) ...\nB) ...\nC) ...\nD) ...\nAnswer: <LETTER>\n\n"
        f"Text:\n{text[:3000]}"
    )
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    data = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7}

    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=30)
        r.raise_for_status()
        resp_json = r.json()
        content = resp_json["choices"][0]["message"]["content"].strip()
        return parse_questions(content)
    except requests.RequestException as e:
        print(f"LLM request failed: {e}")
        return []
    except KeyError:
        return []

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
        send_message(recipient_id, "❌ No quiz could be generated. Please try again.")
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
    question_text = (
        f"{q['question']}\n\n"
        f"A. {q['options']['A']}\n"
        f"B. {q['options']['B']}\n"
        f"C. {q['options']['C']}\n"
        f"D. {q['options']['D']}"
    )
    send_quick_replies(recipient_id, question_text, ["A", "B", "C", "D"])

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
                            extracted_text = extract_text_from_url(file_url)
                            cleaned_text = clean_text(extracted_text)
                            quiz_text = preprocess_for_quiz(cleaned_text)
                            if not quiz_text.strip():
                                send_message(sender_id, "❌ Could not extract meaningful text from this file. Try another file.")
                                send_menu(sender_id)
                                return "ok", 200
                            questions = ai_generate_quiz(quiz_text, num_q=7)
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
            send_message(sender_id, "📝 Enter a topic for the quiz:")
            user_sessions[sender_id] = {"state": "awaiting_topic"}
        elif text.startswith("3"):
            questions = ai_generate_quiz(DEFAULT_KB, num_q=7)
            start_quiz(sender_id, questions)
        else:
            send_menu(sender_id)
    elif sess["state"] == "awaiting_topic":
        questions = ai_generate_quiz(text, num_q=7)
        start_quiz(sender_id, questions)
    elif sess["state"] == "in_quiz":
        handle_answer(sender_id, text)
    elif sess["state"] == "awaiting_file":
        send_message(sender_id, "📄 Please send a file, not text.")
    else:
        send_menu(sender_id)

# --- Get Started Button ---
def setup_get_started_button():
    url = f"https://graph.facebook.com/v17.0/me/messenger_profile?access_token={PAGE_ACCESS_TOKEN}"
    payload = {"get_started": {"payload": "GET_STARTED"}}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"Error setting Get Started: {r.status_code} {r.text}")
        else:
            print("✅ Get Started button set.")
    except requests.RequestException as e:
        print(f"Get Started setup failed: {e}")

if __name__ == "__main__":
    setup_get_started_button()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
