import os
from flask import Flask, request
from facebook_api import send_message, send_quick_replies, send_menu
from file_utils import extract_text_from_url, clean_text
from quiz import generate_quiz_from_text, format_question_message
from session_manager import get_session, set_session
from get_started import setup_get_started_button, handle_postback
from config import VERIFY_TOKEN

app = Flask(__name__)

def start_quiz(user_id, questions):
    if not questions:
        send_message(user_id, "No quiz could be generated. Please try again.")
        send_menu(user_id)
        return
    set_session(user_id, {"state": "in_quiz", "questions": questions, "index": 0, "score": 0})
    ask_question(user_id)

# Updated ask_question to add a "Quit" quick reply
def ask_question(user_id):
    sess = get_session(user_id)
    if not sess:
        send_menu(user_id)
        return
    idx = sess.get("index", 0)
    questions = sess.get("questions", [])
    if idx >= len(questions):
        send_message(user_id, f"✅ Quiz finished! Score: {sess.get('score',0)}/{len(questions)}")
        send_menu(user_id)
        set_session(user_id, {"state": "awaiting_menu"})
        return

    q = questions[idx]
    question_text = format_question_message(q)
    # Add Quit as quick reply option
    send_quick_replies(user_id, question_text, ["A", "B", "C", "D", "Quit"])

# Updated handle_answer to process Quit command
def handle_answer(user_id, text):
    sess = get_session(user_id)
    if not sess or sess.get("state") != "in_quiz":
        send_menu(user_id)
        return

    if text.strip().lower() == "quit":
        send_message(user_id, "🛑 Quiz exited. Returning to main menu.")
        set_session(user_id, {"state": "awaiting_menu"})
        send_menu(user_id)
        return

    idx = sess.get("index", 0)
    questions = sess.get("questions", [])
    if idx >= len(questions):
        send_menu(user_id)
        return

    q = questions[idx]
    user_answer = text.strip().upper()
    correct_answer = q.get("answer", "").upper()
    if user_answer.startswith(correct_answer):
        send_message(user_id, "✅ Correct!")
        sess["score"] = sess.get("score", 0) + 1
    else:
        correct_text = q["options"].get(correct_answer, "N/A")
        send_message(user_id, f"❌ Incorrect. Correct: {correct_answer}) {correct_text}")
    sess["index"] = idx + 1
    set_session(user_id, sess)
    ask_question(user_id)

def handle_text(user_id, text):
    sess = get_session(user_id) or {"state": "awaiting_menu"}

    try:
        if sess["state"] == "awaiting_menu":
            if text.startswith("1"):
                send_message(user_id, "📄 Please upload your file now.")
                set_session(user_id, {"state": "awaiting_file"})
            elif text.startswith("2"):
                send_message(user_id, "📝 Enter a topic or text for quiz generation:")
                set_session(user_id, {"state": "awaiting_topic"})
            elif text.startswith("3"):
                questions = generate_quiz_from_text("General knowledge and facts", num_q=7)
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

    except Exception as e:
        print(f"handle_text error: {e}")
        send_message(user_id, "⚠️ An error occurred. Please try again.")
        send_menu(user_id)

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if token == VERIFY_TOKEN:
            return challenge or "ok"
        return "Invalid token", 403

    data = request.json
    print(f"Webhook data: {data}")

    try:
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
                                    send_message(sender_id, "❌ Could not extract text from the file. Please try another file.")
                                    send_menu(sender_id)
                                    return "ok", 200

                                cleaned_text = clean_text(text)
                                if len(cleaned_text.split()) < 20:
                                    send_message(sender_id, "⚠️ Not enough readable text found. Using general fallback topic.")
                                    cleaned_text = "General knowledge and facts"

                                questions = generate_quiz_from_text(cleaned_text, num_q=7)
                                start_quiz(sender_id, questions)
                                return "ok", 200

                    elif "text" in event["message"]:
                        handle_text(sender_id, event["message"]["text"])

    except Exception as e:
        print(f"Webhook processing error: {e}")

    return "ok", 200

if __name__ == "__main__":
    setup_get_started_button()
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
