import os
import re
import json
import sqlite3
from datetime import datetime
from flask import Flask, request, g, render_template_string
from io import BytesIO
from PyPDF2 import PdfReader
import docx
import requests

app = Flask(__name__)

# Config (set these env vars)
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN", "quiz_000")
PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = "mistralai/mixtral-8x7b-instruct"

DATABASE = "quizbot.db"
LETTERS = ["A", "B", "C", "D", "E", "F", "G"]

# ----------------- DB Helpers -----------------
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            lesson_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            upload_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            lesson_text TEXT,
            quiz_questions TEXT,
            completed INTEGER DEFAULT 0
        );
        """)
        db.execute("""
        CREATE TABLE IF NOT EXISTS user_progress (
            user_id TEXT NOT NULL,
            lesson_id INTEGER NOT NULL,
            question_index INTEGER NOT NULL,
            answered INTEGER DEFAULT 0,
            user_answer TEXT,
            PRIMARY KEY (user_id, lesson_id, question_index)
        );
        """)
        db.commit()

# ----------------- Text extraction -----------------
def extract_text_from_pdf(file_bytes):
    try:
        reader = PdfReader(BytesIO(file_bytes))
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        return text.strip()
    except Exception as e:
        print("PDF extraction error:", e)
        return None

def extract_text_from_docx(file_bytes):
    try:
        doc = docx.Document(BytesIO(file_bytes))
        fullText = [p.text for p in doc.paragraphs if p.text]
        return "\n".join(fullText).strip()
    except Exception as e:
        print("DOCX extraction error:", e)
        return None

def extract_text_from_txt(file_bytes):
    try:
        return file_bytes.decode("utf-8", errors="ignore").strip()
    except Exception as e:
        print("TXT extraction error:", e)
        return None

# ----------------- Messenger send helper -----------------
def fb_send(payload):
    """Send a prepared payload to Facebook Send API."""
    if not PAGE_ACCESS_TOKEN:
        print("No PAGE_ACCESS_TOKEN set — skipping send.")
        return None
    url = "https://graph.facebook.com/v17.0/me/messages"
    params = {"access_token": PAGE_ACCESS_TOKEN}
    try:
        r = requests.post(url, params=params, json=payload, timeout=10)
        print("FB send:", r.status_code, r.text)
        return r
    except Exception as e:
        print("FB send error:", e)
        return None

def send_message(recipient_id, message):
    """
    message may be:
      - a string -> send as text
      - a dict -> treated as the message object (must contain 'text' or 'attachment' or 'quick_replies')
    """
    if not recipient_id:
        print("send_message: missing recipient_id")
        return None

    if isinstance(message, str):
        if not message.strip():
            print("send_message: empty string, skipping")
            return None
        payload = {"recipient": {"id": recipient_id}, "message": {"text": message}}
        return fb_send(payload)

    if isinstance(message, dict):
        # ensure the message dict is valid
        msg = message.get("message") if "message" in message and "recipient" in message else message
        if not msg:
            print("send_message: invalid message dict, skipping")
            return None
        # if msg is empty or lacks content, skip
        if not msg.get("text") and not msg.get("attachment") and not msg.get("quick_replies"):
            print("send_message: message dict has no text/attachment/quick_replies, skipping")
            return None
        payload = {"recipient": {"id": recipient_id}, "message": msg}
        return fb_send(payload)

    print("send_message: unsupported message type, skipping")
    return None

# ----------------- Quiz generation -----------------
def fallback_dummy_quiz():
    return [
        {"question": "Administer _____ if the patient is dehydrated.", "options": ["Fluids", "Oxygen", "Antibiotics"], "answer_index": 0},
        {"question": "National Guidelines are given for the management of _____ .", "options": ["Asthma", "Diabetes", "Hypertension"], "answer_index": 0},
        {"question": "Bronchiectasis is characterized by _____ .", "options": ["permanent dilation", "temporary spasm", "vascular occlusion"], "answer_index": 0}
    ]

def generate_quiz_from_text(text, num_questions=5):
    if not OPENROUTER_API_KEY:
        return fallback_dummy_quiz()

    prompt = f"""
You are a strict quiz generator focused on nursing/medical content.
Given the following lesson text, generate exactly {num_questions} fill-in-the-blank multiple-choice questions.
Return JSON array only, each item has: question (string with '_____'), options (array of 3-4 strings), answer_index (0-based int).
Lesson text:
{text}
"""
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    body = {"model": MODEL, "messages": [{"role": "system", "content": "You are an expert quiz generator."},
                                        {"role": "user", "content": prompt}], "temperature": 0.0}
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body, timeout=30)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
        try:
            parsed = json.loads(content)
            # validate and normalize
            result = []
            for q in parsed:
                if isinstance(q, dict) and "question" in q and "options" in q and "answer_index" in q:
                    result.append({
                        "question": str(q["question"]).strip(),
                        "options": [str(x).strip() for x in q["options"]],
                        "answer_index": int(q["answer_index"])
                    })
            if result:
                return result
        except Exception:
            # try to extract a JSON array substring
            m = re.search(r"(\[.*\])", content, flags=re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(1))
                    return parsed
                except Exception:
                    pass
        print("OpenRouter output not parsable to expected JSON; falling back.")
        return fallback_dummy_quiz()
    except Exception as e:
        print("OpenRouter API error:", e)
        return fallback_dummy_quiz()

# ----------------- DB quiz helper functions -----------------
def store_lesson(user_id, lesson_text, quiz_questions):
    db = get_db()
    quiz_json = json.dumps(quiz_questions)
    cur = db.execute("INSERT INTO lessons (user_id, lesson_text, quiz_questions) VALUES (?, ?, ?)",
                     (user_id, lesson_text, quiz_json))
    db.commit()
    return cur.lastrowid

def get_latest_incomplete_lesson(user_id):
    db = get_db()
    cur = db.execute("SELECT * FROM lessons WHERE user_id = ? AND completed = 0 ORDER BY upload_timestamp DESC LIMIT 1",
                     (user_id,))
    return cur.fetchone()

def get_user_progress(user_id, lesson_id):
    db = get_db()
    cur = db.execute("SELECT * FROM user_progress WHERE user_id = ? AND lesson_id = ? ORDER BY question_index",
                     (user_id, lesson_id))
    rows = cur.fetchall()
    answered = {row["question_index"]: row for row in rows if row["answered"]}
    return answered

def mark_question_answered(user_id, lesson_id, question_index, user_answer):
    db = get_db()
    db.execute("""INSERT OR REPLACE INTO user_progress (user_id, lesson_id, question_index, answered, user_answer)
                  VALUES (?, ?, ?, 1, ?)""", (user_id, lesson_id, question_index, user_answer))
    db.commit()

def mark_lesson_completed(lesson_id):
    db = get_db()
    db.execute("UPDATE lessons SET completed = 1 WHERE lesson_id = ?", (lesson_id,))
    db.commit()

# ----------------- Send next question (quick replies with letters) -----------------
def send_next_question_for_user(user_id):
    lesson_row = get_latest_incomplete_lesson(user_id)
    if not lesson_row:
        send_message(user_id, "No lesson found. Please upload a PDF/DOCX/TXT or paste lesson text to start.")
        return

    lesson_id = lesson_row["lesson_id"]
    quiz = json.loads(lesson_row["quiz_questions"] or "[]")
    if not quiz:
        send_message(user_id, "This lesson has no quiz questions.")
        mark_lesson_completed(lesson_id)
        return

    progress = get_user_progress(user_id, lesson_id)

    for q_idx, q in enumerate(quiz):
        if q_idx not in progress:
            # Build message text showing options
            text = q["question"].strip() + "\n\n"
            for i, opt in enumerate(q["options"]):
                if i >= len(LETTERS):
                    break
                text += f"{LETTERS[i]}) {opt}\n"

            # Build quick replies (letters only). Payload encodes lesson/question/option.
            quick_replies = []
            for i, _opt in enumerate(q["options"]):
                if i >= len(LETTERS):
                    break
                quick_replies.append({
                    "content_type": "text",
                    "title": LETTERS[i],
                    "payload": f"QUIZ_ANSWER|{lesson_id}|{q_idx}|{i}"
                })

            payload = {"text": text, "quick_replies": quick_replies}
            send_message(user_id, payload)
            return q_idx

    # All answered
    send_message(user_id, "🎉 You finished this lesson's quiz! Upload another file to start a new lesson.")
    mark_lesson_completed(lesson_row["lesson_id"])

# ----------------- Webhook routes -----------------
@app.route("/", methods=["GET"])
def index():
    return "Quiz bot running", 200

@app.route("/privacy-policy", methods=["GET"])
def privacy_policy():
    html = """
    <!doctype html>
    <html><head><meta charset="utf-8"><title>Privacy Policy</title></head>
    <body>
      <h1>Privacy Policy</h1>
      <p>This bot processes the files and text you send to create quizzes. Files are used only to generate quizzes and are not shared.</p>
      <p>Contact: your-email@example.com</p>
    </body></html>
    """
    return render_template_string(html)

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        return "Verification token mismatch", 403

    data = request.get_json(silent=True)
    print("Webhook data:", data)

    if not data:
        return "No data", 400

    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                sender_id = event["sender"]["id"]

                # Handle quick_reply (letter buttons)
                msg = event.get("message", {})
                if msg:
                    qr = msg.get("quick_reply")
                    if qr and qr.get("payload"):
                        payload = qr["payload"]
                        if payload.startswith("QUIZ_ANSWER|"):
                            try:
                                _, lesson_id_s, q_index_s, option_idx_s = payload.split("|")
                                lesson_id = int(lesson_id_s); q_index = int(q_index_s); option_idx = int(option_idx_s)
                            except Exception:
                                send_message(sender_id, "Invalid answer payload.")
                                continue

                            db = get_db()
                            row = db.execute("SELECT * FROM lessons WHERE lesson_id = ? AND user_id = ?",
                                             (lesson_id, sender_id)).fetchone()
                            if not row:
                                send_message(sender_id, "Quiz session not found. Upload the lesson again.")
                                continue

                            quiz = json.loads(row["quiz_questions"] or "[]")
                            if q_index < 0 or q_index >= len(quiz):
                                send_message(sender_id, "Invalid question index.")
                                continue

                            selected = option_idx
                            correct_index = int(quiz[q_index].get("answer_index", 0))
                            if selected == correct_index:
                                send_message(sender_id, "✅ Correct!")
                            else:
                                correct_text = quiz[q_index]["options"][correct_index]
                                send_message(sender_id, f"❌ Incorrect. Correct: {LETTERS[correct_index]}) {correct_text}")

                            mark_question_answered(sender_id, lesson_id, q_index, LETTERS[selected])
                            # send next question
                            send_next_question_for_user(sender_id)
                            continue

                # Handle postback (Get Started etc.)
                if event.get("postback"):
                    payload = event["postback"].get("payload", "")
                    if payload == "GET_STARTED":
                        send_message(sender_id, "Welcome — send a PDF/DOCX/TXT or paste lesson text to create a quiz.")
                        continue

                # Handle attachments / files
                if msg and msg.get("attachments"):
                    attachments = msg["attachments"]
                    processed = False
                    for att in attachments:
                        if att.get("type") == "file":
                            file_url = att["payload"].get("url")
                            if not file_url:
                                continue
                            try:
                                r = requests.get(file_url, timeout=20)
                                r.raise_for_status()
                                file_bytes = r.content
                                text = None
                                lc = file_url.lower()
                                content_type = r.headers.get("Content-Type", "").lower()
                                if lc.endswith(".pdf") or "pdf" in content_type:
                                    text = extract_text_from_pdf(file_bytes)
                                elif lc.endswith(".docx") or "word" in content_type:
                                    text = extract_text_from_docx(file_bytes)
                                elif lc.endswith(".txt") or "text" in content_type:
                                    text = extract_text_from_txt(file_bytes)

                                if not text:
                                    send_message(sender_id, "❌ Could not extract text from that file.")
                                    continue

                                send_message(sender_id, "✅ Text extracted. Generating quiz, please wait...")
                                quiz = generate_quiz_from_text(text, num_questions=5)
                                if not quiz:
                                    send_message(sender_id, "❌ Couldn't generate quiz from text.")
                                    continue

                                lesson_id = store_lesson(sender_id, text, quiz)
                                send_message(sender_id, "✅ Lesson saved. Starting quiz now...")
                                send_next_question_for_user(sender_id)
                                processed = True
                            except Exception as e:
                                print("File processing error:", e)
                                send_message(sender_id, "❌ Error processing your file.")
                    if processed:
                        continue

                # Plain text message fallback (treat as pasted lesson text or trigger quiz)
                if msg and msg.get("text"):
                    txt = msg.get("text").strip()
                    # commands
                    if txt.lower() in ("quiz", "start"):
                        send_next_question_for_user(sender_id)
                    else:
                        # save as lesson text
                        quiz = generate_quiz_from_text(txt, num_questions=5)
                        if not quiz:
                            send_message(sender_id, "❌ Couldn't generate quiz from provided text.")
                            continue
                        lesson_id = store_lesson(sender_id, txt, quiz)
                        send_message(sender_id, "✅ Lesson saved. Starting quiz now...")
                        send_next_question_for_user(sender_id)

    return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
