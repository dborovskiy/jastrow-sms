from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
import requests
import os
from urllib.parse import quote

app = Flask(__name__)

LEXICON = "Jastrow Dictionary"

HEBREW_LETTERS = {
    "aleph": "א", "alef": "א", "א": "א",
    "bet": "ב", "beit": "ב", "beis": "ב", "ב": "ב",
    "gimel": "ג", "gimmel": "ג", "ג": "ג",
    "dalet": "ד", "daled": "ד", "ד": "ד",
    "hey": "ה", "heh": "ה", "hei": "ה", "ה": "ה",
    "vav": "ו", "waw": "ו", "ו": "ו",
    "zayin": "ז", "zain": "ז", "ז": "ז",
    "chet": "ח", "ches": "ח", "het": "ח", "ח": "ח",
    "tet": "ט", "tes": "ט", "ט": "ט",
    "yud": "י", "yod": "י", "י": "י",
    "kaf": "כ", "chaf": "כ", "khaf": "כ", "כ": "כ",
    "final kaf": "ך", "final chaf": "ך", "ך": "ך",
    "lamed": "ל", "ל": "ל",
    "mem": "מ", "מ": "מ",
    "final mem": "ם", "ם": "ם",
    "nun": "נ", "נ": "נ",
    "final nun": "ן", "ן": "ן",
    "samech": "ס", "samekh": "ס", "ס": "ס",
    "ayin": "ע", "ע": "ע",
    "pey": "פ", "peh": "פ", "pei": "פ", "פ": "פ",
    "fey": "פ", "feh": "פ",
    "final pey": "ף", "final peh": "ף", "final fey": "ף", "ף": "ף",
    "tzadi": "צ", "tsadi": "צ", "tzaddik": "צ", "צ": "צ",
    "final tzadi": "ץ", "final tsadi": "ץ", "ץ": "ץ",
    "kuf": "ק", "koof": "ק", "qof": "ק", "ק": "ק",
    "resh": "ר", "ר": "ר",
    "shin": "ש", "sin": "ש", "ש": "ש",
    "tav": "ת", "taf": "ת", "sav": "ת", "ת": "ת",
}

def spelled_letters_to_hebrew(text: str) -> str:
    text = text.lower().replace(",", " ").replace(".", " ")
    words = text.split()

    letters = []
    i = 0
    while i < len(words):
        two_word = " ".join(words[i:i+2])
        if two_word in HEBREW_LETTERS:
            letters.append(HEBREW_LETTERS[two_word])
            i += 2
        elif words[i] in HEBREW_LETTERS:
            letters.append(HEBREW_LETTERS[words[i]])
            i += 1
        else:
            i += 1

    return "".join(letters)

def lookup_jastrow(word: str) -> str:
    word = word.strip()
    if not word:
        return "No word detected."

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

def send_sms(to_number: str, body: str):
    client = Client(
        os.environ["TWILIO_ACCOUNT_SID"],
        os.environ["TWILIO_AUTH_TOKEN"]
    )

    client.messages.create(
        body=body[:1500],
        from_=os.environ["TWILIO_PHONE_NUMBER"],
        to=to_number
    )

@app.route("/", methods=["GET"])
def home():
    return "Jastrow SMS/Voice app is running."

@app.route("/sms", methods=["POST"])
def sms():
    incoming = request.form.get("Body", "").strip()

    response = MessagingResponse()
    response.message(lookup_jastrow(incoming)[:1500])

    return str(response), 200, {"Content-Type": "application/xml"}

@app.route("/voice", methods=["POST"])
def voice():
    response = VoiceResponse()

    gather = Gather(
        input="speech",
        action="/voice-result",
        method="POST",
        language="en-US",
        speech_timeout="auto"
    )

    gather.say(
        "Spell the Hebrew word using letter names. "
        "For example, say: shin, bet, tav."
    )

    response.append(gather)
    response.say("I did not hear anything. Please try again.")
    response.redirect("/voice")

    return str(response), 200, {"Content-Type": "application/xml"}

@app.route("/voice-result", methods=["POST"])
def voice_result():
    spoken = request.form.get("SpeechResult", "").strip()
    caller = request.form.get("From")

    hebrew_word = spelled_letters_to_hebrew(spoken)

    response = VoiceResponse()

    if not hebrew_word:
        response.say("Sorry, I could not understand the letters. Please try again.")
        response.redirect("/voice")
        return str(response), 200, {"Content-Type": "application/xml"}

    result = lookup_jastrow(hebrew_word)

    if caller:
        send_sms(caller, f"You spelled: {hebrew_word}\n\n{result}")
        response.say("I found the word. I texted you the result.")
    else:
        response.say("I found the word, but I could not text the result.")

    return str(response), 200, {"Content-Type": "application/xml"}

if __name__ == "__main__":
    app.run(debug=True)