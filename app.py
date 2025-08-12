import os
import re
import json
import time
import requests
from flask import Flask, request, render_template_string
from io import BytesIO
from PyPDF2 import PdfReader
import docx

# ----------------- Config -----------------
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN", "quiz_000")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = "mistralai/mixtral-8x7b-instruct"

if not PAGE_ACCESS_TOKEN:
    print("Warning: PAGE_ACCESS_TOKEN not set. Bot will not be able to send messages.")
if not OPENROUTER_API_KEY:
    print("Warning: OPENROUTER_API_KEY not set. Bot will use fallback questions.")

LETTERS = ["A", "B", "C", "D", "E", "F", "G"]

app = Flask(__name__)

# ----------------- In-memory sessions (stateless for now) -----------------
# user_sessions[sender_id] = {
#   "mode": "menu" | "waiting_upload" | "file_quiz" | "random_quiz",
#   "lesson_text": "...",
#   "quiz": [ { "question": "...", "options": [...], "answer_index": 0 }, ...],
#   "index": 0,
#   "score": 0
# }
user_sessions = {}

# ----------------- Helpers: Facebook Send API -----------------
GRAPH_URL = "https://graph.facebook.com/v17.0/me/messages"

def fb_send(payload):
    if not PAGE_ACCESS_TOKEN:
        print("fb_send skipped: PAGE_ACCESS_TOKEN not set")
        return None
    try:
        r = requests.post(GRAPH_URL, params={"access_token": PAGE_ACCESS_TOKEN}, json=payload, timeout=10)
        print("FB send:", r.status_code, r.text)
        return r
    except Exception as e:
        print("FB send error:", e)
        return None

def send_text(recipient_id, text):
    if not text or not text.strip():
        print("skip empty text send")
        return
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    return fb_send(payload)

def send_menu(recipient_id):
    # Use button template (max 3 buttons) for the main menu
    buttons = [
        {"type": "postback", "title": "📤 Upload File Topic", "payload": "MENU_UPLOAD"},
        {"type": "postback", "title": "📚 Random Quiz (Advanced Nursing)", "payload": "MENU_RANDOM"},
        {"type": "postback", "title": "❓ Help", "payload": "MENU_HELP"},
    ]
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": "Main Menu — choose an option:",
                    "buttons": buttons
                }
            }
        }
    }
    return fb_send(payload)

def send_quick_replies_letters(recipient_id, question_text, options, base_payload_prefix="QUIZ_ANSWER"):
    """
    Sends the question text (with options listed) and quick replies labeled A,B,C,... .
    Quick reply payload format: "{base_payload_prefix}|{q_index}|{option_idx}"
    Caller must ensure q_index and option indices map to the user's session.
    """
    # build labeled question text
    text = question_text.strip() + "\n\n"
    for i, opt in enumerate(options):
        if i >= len(LETTERS):
            break
        text += f"{LETTERS[i]}) {opt}\n"

    quick_replies = []
    for i, _ in enumerate(options):
        if i >= len(LETTERS):
            break
        quick_replies.append({
            "content_type": "text",
            "title": LETTERS[i],
            "payload": f"{base_payload_prefix}|{i}"
        })

    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text, "quick_replies": quick_replies}
    }
    return fb_send(payload)

# ----------------- File text extraction -----------------
def extract_text_from_pdf_bytes(file_bytes):
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
        return ""

def extract_text_from_docx_bytes(file_bytes):
    try:
        doc = docx.Document(BytesIO(file_bytes))
        paras = [p.text for p in doc.paragraphs if p.text]
        return "\n".join(paras).strip()
    except Exception as e:
        print("DOCX extraction error:", e)
        return ""

def extract_text_from_txt_bytes(file_bytes):
    try:
        return file_bytes.decode("utf-8", errors="ignore").strip()
    except Exception as e:
        print("TXT extraction error:", e)
        return ""

# ----------------- Quiz generation (OpenRouter) -----------------
def generate_quiz_openrouter(lesson_text, num_questions=5):
    """
    Ask OpenRouter to generate JSON array of questions.
    Each question: { "question": "...", "options": ["...","...","...","..."], "answer_index": 0 }
    """
    if not OPENROUTER_API_KEY:
        print("OpenRouter key missing -> fallback")
        return fallback_quiz_from_text(lesson_text, num_questions)

    prompt = f"""
You are an expert nursing quiz generator.

Given the lesson text below, produce exactly {num_questions} multiple-choice questions focused on the nursing/medical content.
Each question must be fill-in-the-blank style or a concise MCQ.
Return output strictly as a JSON array. Each array element must be an object with keys:
- question: string (include '_____' if fill-in-the-blank)
- options: array of 3 or 4 short strings
- answer_index: integer (0-based index of the correct option)

Example output:
[
  {{"question":"Administer _____ if patient is dehydrated.","options":["Fluids","Oxygen","Antibiotics","Sedation"],"answer_index":0}},
  ...
]

Lesson text:
{lesson_text[:2400]}
"""
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    body = {"model": OPENROUTER_MODEL,
            "messages": [{"role": "system", "content": "You are an expert quiz generator."},
                         {"role": "user", "content": prompt}],
            "temperature": 0.0}

    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body, timeout=30)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
        # Try parse JSON directly
        try:
            parsed = json.loads(content)
            validated = []
            for q in parsed:
                if isinstance(q, dict) and "question" in q and "options" in q and "answer_index" in q:
                    # sanitize
                    options = [str(o).strip() for o in q["options"]][:4]
                    validated.append({"question": str(q["question"]).strip(),
                                      "options": options,
                                      "answer_index": int(q["answer_index"])})
            if validated:
                return validated
        except Exception:
            # try find JSON substring
            m = re.search(r"(\[.*\])", content, flags=re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(1))
                    return parsed
                except Exception:
                    pass
        print("OpenRouter output not parseable or invalid -> fallback")
        return fallback_quiz_from_text(lesson_text, num_questions)
    except Exception as e:
        print("OpenRouter API error:", e)
        return fallback_quiz_from_text(lesson_text, num_questions)

def fallback_quiz_from_text(lesson_text, num_questions=5):
    # Very simple fallback: return generic questions (keeps UI working)
    # Attempt to pick nouns/terms from lesson_text? Keep it simple and safe.
    q = []
    for i in range(num_questions):
        q.append({
            "question": f"Fallback Q{i+1}: Administer _____ if the patient is dehydrated.",
            "options": ["Fluids", "Oxygen", "Antibiotics", "Sedation"],
            "answer_index": 0
        })
    return q

# ----------------- Session & flow helpers -----------------
def start_file_quiz_session(user_id, lesson_text, num_questions=5):
    quiz = generate_quiz_openrouter(lesson_text, num_questions=num_questions)
    user_sessions[user_id] = {
        "mode": "file_quiz",
        "lesson_text": lesson_text,
        "quiz": quiz,
        "index": 0,
        "score": 0
    }
    send_text(user_id, f"📝 Quiz ready — {len(quiz)} questions generated. Starting now.")
    send_current_question(user_id)

def start_random_quiz_session(user_id, num_questions=5):
    lesson_text = "Advanced Nursing"
    quiz = generate_quiz_openrouter(lesson_text, num_questions=num_questions)
    user_sessions[user_id] = {
        "mode": "random_quiz",
        "lesson_text": lesson_text,
        "quiz": quiz,
        "index": 0,
        "score": 0
    }
    send_text(user_id, f"🧠 Advanced Nursing quiz ready — {len(quiz)} questions. Starting now.")
    send_current_question(user_id)

def send_current_question(user_id):
    session = user_sessions.get(user_id)
    if not session:
        send_menu(user_id)
        return
    idx = session.get("index", 0)
    quiz = session.get("quiz", [])
    if idx >= len(quiz):
        # finished
        score = session.get("score", 0)
        total = len(quiz)
        send_text(user_id, f"🏁 Quiz finished. Score: {score}/{total}.")
        # clear session and show menu
        user_sessions.pop(user_id, None)
        send_menu(user_id)
        return
    q = quiz[idx]
    # send question with options listed and quick replies labeled A,B,C...
    send_quick_replies_letters(user_id, q["question"], q["options"], base_payload_prefix=f"QUIZ_ANSWER|{idx}")

# ----------------- Webhook & routing -----------------
@app.route("/", methods=["GET"])
def index():
    return "Quiz bot running", 200

@app.route("/privacy-policy", methods=["GET"])
def privacy_policy():
    html = """
    <html><head><title>Privacy Policy</title></head>
    <body>
    <h1>Privacy Policy</h1>
    <p>This bot processes files you upload to generate quizzes. Uploaded files are used only to generate quizzes and are not shared.</p>
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

    payload = request.get_json(silent=True)
    print("Webhook data:", payload)
    if not payload:
        return "No data", 400

    if payload.get("object") != "page":
        return "Not a page event", 200

    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = event["sender"]["id"]

            # Quick reply chosen (letter)
            msg = event.get("message", {})
            if msg and "quick_reply" in msg and msg["quick_reply"].get("payload"):
                qr_payload = msg["quick_reply"]["payload"]
                # qr_payload format: "QUIZ_ANSWER|{q_idx}|{option_idx}" or "QUIZ_ANSWER|{q_idx}" with option index encoded in payload we sent earlier
                # We used pattern base_payload_prefix|{option_idx} when sending; here parse
                if qr_payload.startswith("QUIZ_ANSWER|"):
                    try:
                        parts = qr_payload.split("|")
                        # format we used: QUIZ_ANSWER|{option_idx} OR QUIZ_ANSWER|{idx}|{option_idx}?
                        # For robustness, handle both:
                        if len(parts) == 2:
                            # legacy: only option index present; use current session index
                            option_idx = int(parts[1])
                            session = user_sessions.get(sender_id)
                            if not session:
                                send_menu(sender_id)
                                continue
                            q_idx = session.get("index", 0)
                        elif len(parts) == 3 and parts[1].isdigit():
                            # we used base_payload_prefix|{idx} earlier; option idx comes from quick reply ordering
                            # Actually in send_quick_replies_letters we used base_payload_prefix|{i} where i is option idx
                            option_idx = int(parts[1])
                            # q_idx is session index
                            session = user_sessions.get(sender_id)
                            if not session:
                                send_menu(sender_id)
                                continue
                            q_idx = session.get("index", 0)
                        else:
                            # fallback
                            send_text(sender_id, "Invalid answer payload.")
                            continue
                    except Exception:
                        send_text(sender_id, "Invalid answer payload format.")
                        continue

                    # Validate and score
                    session = user_sessions.get(sender_id)
                    if not session:
                        send_menu(sender_id)
                        continue
                    quiz = session.get("quiz", [])
                    idx = session.get("index", 0)
                    if idx >= len(quiz):
                        send_text(sender_id, "This quiz is already finished.")
                        send_menu(sender_id)
                        continue
                    correct_index = int(quiz[idx].get("answer_index", 0))
                    if option_idx == correct_index:
                        session["score"] = session.get("score", 0) + 1
                        send_text(sender_id, "✅ Correct!")
                    else:
                        correct_text = quiz[idx]["options"][correct_index] if 0 <= correct_index < len(quiz[idx]["options"]) else "Unknown"
                        send_text(sender_id, f"❌ Incorrect. Correct: {LETTERS[correct_index]}) {correct_text}")

                    # advance
                    session["index"] = idx + 1
                    # small pause optional (not needed)
                    send_current_question(sender_id)
                    continue

            # Postback (menu buttons)
            if event.get("postback"):
                payload_str = event["postback"].get("payload", "")
                if payload_str == "MENU_UPLOAD":
                    user_sessions[sender_id] = {"mode": "waiting_upload", "lesson_text": "", "quiz": [], "index": 0, "score": 0}
                    send_text(sender_id, "📤 Please upload your PDF, DOCX or TXT file now.")
                    continue
                elif payload_str == "MENU_RANDOM":
                    # start immediate random quiz (Advanced Nursing)
                    send_text(sender_id, "🎯 Generating random Advanced Nursing quiz (5 questions)...")
                    start_random_quiz_session(sender_id, num_questions=5)
                    continue
                elif payload_str == "MENU_HELP":
                    send_text(sender_id, "Menu:\n• Upload File Topic — upload a file and I will make a quiz from it.\n• Random Quiz — advanced nursing questions.\nAfter a quiz finishes the menu will be shown again.")
                    send_menu(sender_id)
                    continue
                elif payload_str.startswith("GET_STARTED"):
                    send_menu(sender_id)
                    continue

            # Attachments -> file uploaded
            if msg and msg.get("attachments"):
                atts = msg.get("attachments", [])
                handled_any = False
                for att in atts:
                    att_type = att.get("type")
                    payload_url = att.get("payload", {}).get("url")
                    if not payload_url:
                        continue
                    session = user_sessions.get(sender_id, {})
                    if session.get("mode") == "waiting_upload":
                        try:
                            r = requests.get(payload_url, timeout=20)
                            r.raise_for_status()
                            content = r.content
                            lc = payload_url.lower()
                            text = ""
                            ct = r.headers.get("Content-Type", "").lower()
                            if lc.endswith(".pdf") or "pdf" in ct:
                                text = extract_text_from_pdf_bytes(content)
                            elif lc.endswith(".docx") or "word" in ct:
                                text = extract_text_from_docx_bytes(content)
                            elif lc.endswith(".txt") or "text" in ct:
                                text = extract_text_from_txt_bytes(content)
                            else:
                                # unknown type, try pdf/docx/text attempts
                                text = extract_text_from_pdf_bytes(content) or extract_text_from_docx_bytes(content) or extract_text_from_txt_bytes(content)
                            if not text:
                                send_text(sender_id, "❌ Couldn't extract text from the uploaded file. Make sure it's PDF/DOCX/TXT.")
                                send_menu(sender_id)
                                handled_any = True
                                continue
                            # start quiz session from file text (generate 5-10 questions)
                            send_text(sender_id, "✅ File received. Generating quiz from your file (5 questions)...")
                            start_file_quiz_session(sender_id, text, num_questions=5)
                            handled_any = True
                        except Exception as e:
                            print("file download/extract error:", e)
                            send_text(sender_id, "❌ Error processing file.")
                            send_menu(sender_id)
                            handled_any = True
                if handled_any:
                    continue

            # If plain text and not in a special mode, show the menu (or accept a 'start' command)
            if msg and msg.get("text"):
                txt = msg.get("text", "").strip().lower()
                session = user_sessions.get(sender_id)
                if session and session.get("mode") == "waiting_upload":
                    send_text(sender_id, "📤 Waiting for file upload. Please upload your PDF/DOCX/TXT now.")
                elif txt in ("menu", "start", "help"):
                    send_menu(sender_id)
                else:
                    # default: show menu
                    send_menu(sender_id)

    return "EVENT_RECEIVED", 200

# ----------------- Run -----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("Starting app on port", port)
    app.run(host="0.0.0.0", port=port)
