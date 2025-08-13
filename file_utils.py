import requests
import re
from io import BytesIO
from PyPDF2 import PdfReader
import docx

def clean_text(text):
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'/[A-Za-z0-9]+', '', text)
    text = re.sub(r'[^\x20-\x7E]+', ' ', text)
    text = re.sub(r'\b(?:BT|ET|Tf|Td|Tj|EMC)\b', '', text)
    lines = [line for line in text.splitlines() if re.search(r'[A-Za-z]', line)]
    return ' '.join(lines).strip()

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
