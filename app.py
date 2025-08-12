import os
import re
import json
import sqlite3
from datetime import datetime
from flask import Flask, request, g
from io import BytesIO
from PyPDF2 import PdfReader
import docx
import requests

app = Flask(__name__)

VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = "mistralai/mixtral-8x7b-instruct"

DATABASE = "quizbot.db"

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
        fullText = []
        for para in doc.paragraphs:
            fullText.append(para.text)
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

def send_message(recipient_id, message_payload):
    url = f"https://graph.facebook.com/v17.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    payload = {
        "recipient": {"id": recipient_id},
        "message": message_payload
    }
    r = requests.post(url, json=payload)
    print(f"Sent message to {recipient_id}: {r.status_code}, {r.text}")

def generate_quiz_from_text(text, num_questions=3):
    if not OPENROUTER_API_KEY:
        # fallback dummy quiz
        return [
            {
                "question": "What is the main organ affected in COPD?",
                "options": ["Heart", "Lungs", "Kidneys", "Liver"],
                "answer": "Lungs"
            },
            {
                "question": "Which vitamin is essential for blood clotting?",
                "options": ["Vitamin A", "Vitamin C", "Vitamin K", "Vitamin D"],
                "answer": "Vitamin K"
            },
            {
                "question": "Administer _____ if the patient is dehydrated.",
                "options": ["Oxygen", "Fluids", "Antibiotics", "Sedatives"],
                "answer": "Fluids"
            }
        ]

    prompt = f"""
Create {num_questions} multiple-choice quiz questions based on the following text.
Focus on the medical/nursing topic.
For each question:
- Make it fill-in-the-blank style
- Provide 4 options, labeled a), b), c), d)
- Clearly state the correct answer.
Text:
{text}
"""

    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    data = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are an expert quiz generator."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3
    }

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=20
        )
        response.raise_for_status()
        result_text = response.json()["choices"][0]["message"]["content"]
        return parse_quiz_text(result_text)
    except Exception as e:
        print("OpenRouter API error:", e)
        return []

def parse_quiz_text(text):
    quizzes = []
    pattern = re.compile(
        r"\d+\.\s*(.*?)\n"
        r"a\)\s*(.*?)\n"
        r"b\)\s*(.*?)\n"
        r"c\)\s*(.*?)\n"
        r"d\)\s*(.*?)\n"
        r"Answer:\s*([abcd])\)\s*(.*)",
        re.IGNORECASE | re.DOTALL
    )

    matches = pattern.findall(text)
    for m in matches:
        q, a, b, c, d, ans_letter, ans_text = m
        options = [a.strip(), b.strip(), c.strip(), d.strip()]
        ans_index = ord(ans_letter.lower()) - ord('a')
        quizzes.append({
            "question": q.strip(),
            "options": options,
            "answer": options[ans_index]
        })
    return quizzes

def store_lesson(user_id, lesson_text, quiz_questions):
    db = get_db()
    quiz_json = json.dumps(quiz_questions)
    cur = db.execute(
        "INSERT INTO lessons (user_id, lesson_text, quiz_questions) VALUES (?, ?, ?)",
        (user_id, lesson_text, quiz_json)
    )
    db.commit()
    return cur.lastrowid

def get_latest_incomplete_lesson(user_id):
    db = get_db()
    cur = db.execute(
        "SELECT * FROM lessons WHERE user_id = ? AND completed = 0 ORDER BY upload_timestamp DESC LIMIT 1",
        (user_id,)
    )
    return cur.fetchone()

def get_user_progress(user_id, lesson_id):
    db = get_db()
    cur = db.execute(
        "SELECT * FROM user_progress WHERE user_id = ? AND lesson_id = ? ORDER BY question_index",
        (user_id, lesson_id)
    )
    rows = cur.fetchall()
    answered = {row["question_index"]: row for row in rows if row["answered"]}
    return answered

def mark_question_answered(user_id, lesson_id, question_index, user_answer):
    db = get_db()
    db.execute("""
        INSERT OR REPLACE INTO user_progress (user_id, lesson_id, question_index, answered, user_answer)
        VALUES (?, ?, ?, 1, ?)
    """, (user_id, lesson_id, question_index, user_answer))
    db.commit()

def mark_lesson_completed(lesson_id):
    db = get_db()
    db.execute("UPDATE lessons SET completed = 1 WHERE lesson_id = ?", (lesson_id,))
    db.commit()

def send_quiz_question_from_db(recipient_id, lesson_row):
    lesson_id = lesson_row["lesson_id"]
    quiz = json.loads(lesson_row["quiz_questions"])
    user_progress = get_user_progress(recipient_id, lesson_id)
    for idx, q in enumerate(quiz):
        if idx not in user_progress:
            buttons = []
            for option_idx, option in enumerate(q["options"][:3]):
                buttons.append({
                    "type": "postback",
                    "title": option if len(option) <= 20 else option[:17] + "...",
                    "payload": f"QUIZ_ANSWER_{lesson_id}_{idx}_{option_idx}"
                })
            if not buttons:
                send_message(recipient_id, "⚠️ No valid options available for the question.")
                return None
            message_payload = {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "button",
                        "text": q["question"],
                        "buttons": buttons
                    }
                }
            }
            send_message(recipient_id, message_payload)
            return idx
    send_message(recipient_id, "🎉 You finished this lesson's quiz! Upload a new file to try again.")
    mark_lesson_completed(lesson_id)
    return None

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        return "Verification token mismatch", 403

    if request.method == "POST":
        data = request.get_json()
        print("Webhook data:", data)

        if data.get("object") == "page":
            for entry in data.get("entry", []):
                for event in entry.get("messaging", []):
                    sender_id = event["sender"]["id"]

                    # Handle button postbacks for quiz answers
                    if event.get("postback"):
                        payload = event["postback"].get("payload", "")
                        if payload.startswith("QUIZ_ANSWER_"):
                            parts = payload.split("_")
                            lesson_id = int(parts[2])
                            question_index = int(parts[3])
                            option_idx = int(parts[4])

                            db = get_db()
                            lesson = db.execute(
                                "SELECT * FROM lessons WHERE lesson_id = ? AND user_id = ?",
                                (lesson_id, sender_id)
                            ).fetchone()
                            if not lesson:
                                send_message(sender_id, "Quiz session expired or invalid. Please upload a new file.")
                                continue

                            quiz = json.loads(lesson["quiz_questions"])
                            if question_index >= len(quiz):
                                send_message(sender_id, "Invalid question index.")
                                continue

                            selected_option = quiz[question_index]["options"][option_idx]
                            correct_answer = quiz[question_index]["answer"]

                            if selected_option == correct_answer:
                                send_message(sender_id, "✅ Correct!")
                            else:
                                send_message(sender_id, f"❌ Incorrect. The correct answer is: {correct_answer}")

                            mark_question_answered(sender_id, lesson_id, question_index, selected_option)

                            # Send next unanswered question or finish
                            lesson_row = db.execute(
                                "SELECT * FROM lessons WHERE lesson_id = ?", (lesson_id,)
                            ).fetchone()
                            send_quiz_question_from_db(sender_id, lesson_row)
                        continue

                    # Handle file upload message
                    message = event.get("message")
                    if message:
                        attachments = message.get("attachments", [])
                        if attachments:
                            for att in attachments:
                                if att.get("type") == "file":
                                    file_url = att["payload"]["url"]
                                    try:
                                        file_resp = requests.get(file_url, timeout=15)
                                        file_resp.raise_for_status()
                                        file_bytes = file_resp.content
                                        content_type = file_resp.headers.get("Content-Type", "").lower()

                                        text = None
                                        if "pdf" in content_type or file_url.lower().endswith(".pdf"):
                                            text = extract_text_from_pdf(file_bytes)
                                        elif "word" in content_type or file_url.lower().endswith((".docx", ".doc")):
                                            text = extract_text_from_docx(file_bytes)
                                        elif "text" in content_type or file_url.lower().endswith(".txt"):
                                            text = extract_text_from_txt(file_bytes)

                                        if not text:
                                            send_message(sender_id, "❌ Could not extract text from your file.")
                                            continue

                                        send_message(sender_id, "✅ Text extracted. Generating quiz, please wait...")

                                        quiz = generate_quiz_from_text(text, num_questions=5)
                                        if not quiz:
                                            send_message(sender_id, "❌ Sorry, I couldn't generate a quiz from the text.")
                                            continue

                                        lesson_id = store_lesson(sender_id, text, quiz)
                                        send_message(sender_id, "✅ Lesson saved. Starting quiz now...")
                                        lesson_row = get_latest_incomplete_lesson(sender_id)
                                        send_quiz_question_from_db(sender_id, lesson_row)

                                    except Exception as e:
                                        print("File processing error:", e)
                                        send_message(sender_id, "❌ Error processing your file.")
                        else:
                            # If user just sends text or no file
                            # Trigger daily quiz question (latest incomplete lesson)
                            lesson_row = get_latest_incomplete_lesson(sender_id)
                            if lesson_row:
                                send_quiz_question_from_db(sender_id, lesson_row)
                            else:
                                send_message(sender_id, "Please upload a PDF, DOCX, or TXT file to generate a quiz.")

        return "EVENT_RECEIVED", 200

@app.route("/privacy-policy")
def privacy_policy():
    return """
    <h1>Privacy Policy</h1>
    <p>This bot processes files you upload to generate quizzes. No personal data is stored beyond lesson progress.</p>
    """

if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
