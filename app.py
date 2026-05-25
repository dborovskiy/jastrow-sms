from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import requests
from urllib.parse import quote

app = Flask(__name__)

LEXICON = "Jastrow Dictionary"

def lookup_jastrow(word: str) -> str:
    word = word.strip()
    if not word:
        return "Send me one Hebrew/Aramaic word."

    url = f"https://www.sefaria.org/api/words/completion/{quote(word)}/{quote(LEXICON)}"
    matches = requests.get(url, timeout=10).json()

    if not matches:
        return f"No Jastrow result found for: {word}"

    lines = []
    for match in matches[:3]:
        plain = match[0]
        pointed = match[1] if len(match) > 1 else plain
        sefaria_link = f"https://www.sefaria.org/Jastrow,_Dictionary.{quote(plain)}"
        lines.append(f"{pointed}\n{sefaria_link}")

    return "Top Jastrow matches:\n\n" + "\n\n".join(lines)

@app.route("/", methods=["GET"])
def home():
    return "Jastrow SMS app is running."

@app.route("/sms", methods=["POST"])
def sms():
    incoming = request.form.get("Body", "").strip()

    response = MessagingResponse()
    response.message(lookup_jastrow(incoming)[:1500])

    return str(response), 200, {"Content-Type": "application/xml"}

if __name__ == "__main__":
    app.run(debug=True)