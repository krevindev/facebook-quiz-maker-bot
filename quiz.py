import re
import requests
from config import OPENROUTER_API_KEY, MODEL

def generate_quiz_from_text(text, num_q=5):
    prompt = (
        f"Generate {num_q} multiple-choice questions (A-D) from the following text.\n"
        f"Only create questions relevant to the main topics and lessons.\n\n"
        f"Use this strict format:\n"
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
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=30)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        return parse_questions(text)
    except requests.Timeout:
        print("LLM request timed out")
    except Exception as e:
        print(f"LLM error: {e}")
    return []

def parse_questions(raw):
    try:
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
    except Exception as e:
        print(f"parse_questions error: {e}")
        return []

def format_question_message(question_obj):
    try:
        return (
            f"\n{question_obj['question']}\n"
            f"A. {question_obj['options']['A']}\n"
            f"B. {question_obj['options']['B']}\n"
            f"C. {question_obj['options']['C']}\n"
            f"D. {question_obj['options']['D']}"
        )
    except Exception as e:
        print(f"format_question_message error: {e}")
        return "Error formatting question."
