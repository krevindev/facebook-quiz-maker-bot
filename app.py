from flask import Flask, request
from facebook_api import send_message, send_quick_replies, send_menu
from file_utils import extract_text_from_url, clean_text
from quiz import generate_quiz_from_text, format_question_message
from session_manager import get_session, set_session
from get_started import setup_get_started_button, handle_postback
import os

app = Flask(__name__)

def start_quiz(user_id, questions):
    if not questions:
        send_message(user_id, "No quiz could be generated. Please try again.")
        send_menu(user_id)
        return
    set_session(user_id, {"state": "in_quiz", "questions": questions, "index": 0, "score": 0})
    ask_question(user_id)

def ask_question(user_id):
    sess = get_session(user_id)
    idx = sess["index"]
    if idx >= len(sess["questions"]):
        send_message(user_id, f"✅ Quiz finished! Score: {sess['score']}/{len(sess['questions'])}")
        send_menu(user_id)
        return
    
    q = sess["questions"][idx]
    question_text = format_question_message(q)
    send_quick_replies(user_id, question_text, ["A", "B", "C", "D"])

def handle_answer(user_id, text):
    sess = get_session(user_id)
    if not sess or sess.get("state") != "in_quiz":
        send_menu(user_id)
        return
    q = sess["questions"][sess["index"]]
    if text.strip().upper().startswith(q["answer"]):
        send_message(user_id, "✅ Correct!")
        sess["score"] += 1
    else:
        send_message(user_id, f"❌ Incorrect. Correct: {q['answer']}) {q['options'][q['answer']]}")
    sess["index"] += 1
    ask_question(user_id)

def handle_text(user_id, text):
    sess = get_session(user_id) or {"state": "awaiting_menu"}

    if sess["state"] == "awaiting_menu":
        if text.startswith("1"):
            send_message(user_id, "📄 Please upload your file now.")
            set_session(user_id, {"state": "awaiting_file"})
        elif text.startswith("2"):
            send_message(user_id, "📝 Enter a medical topic:")
            set_session(user_id, {"state": "awaiting_topic"})
        elif text.startswith("3"):
            questions = generate_quiz_from_text("Advanced Nursing concepts", num_q=7)
            start_quiz(user_id, questions)
        else:
            send_menu(user_id)

    elif sess["state"] == "awaiting_topic":
        questions = generate_quiz_from_text(text, num_q=7)
        start_quiz(user_id, questions)

    elif sess["state"] == "in_quiz":
        handle_answer(user_id, text)

    elif sess["state"] == "awaiting_file":
        send_message(user_id, "📄 Please send a file, not text.")

    else:
        send_menu(user_id)

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == setup_get_started_button.VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Invalid token", 403

    data = request.json
    print(f"Webhook data: {data}")
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = event["sender"]["id"]

            if "postback" in event:
                payload = event["postback"].get("payload")
                handle_postback(sender_id, payload, send_message, set_session)
                continue

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

if __name__ == "__main__":
    setup_get_started_button()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
