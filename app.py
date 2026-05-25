from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

app = Flask(__name__)

LEXICON = "Jastrow Dictionary"

HEBREW_GEMATRIA = {
    "1": "א", "2": "ב", "3": "ג", "4": "ד", "5": "ה",
    "6": "ו", "7": "ז", "8": "ח", "9": "ט", "10": "י",
    "20": "כ", "30": "ל", "40": "מ", "50": "נ", "60": "ס",
    "70": "ע", "80": "פ", "90": "צ", "100": "ק",
    "200": "ר", "300": "ש", "400": "ת",
}

FINAL_FORMS = {
    "20": "ך", "40": "ם", "50": "ן", "80": "ף", "90": "ץ",
}


def keypad_to_hebrew(digits: str) -> str:
    digits = digits.strip().replace("#", "")

    letters = []
    current = ""
    i = 0

    while i < len(digits):
        ch = digits[i]

        if ch.isdigit():
            current += ch
        elif ch == "*":
            if i + 1 < len(digits) and digits[i + 1] == "*":
                if current in FINAL_FORMS:
                    letters.append(FINAL_FORMS[current])
                elif current in HEBREW_GEMATRIA:
                    letters.append(HEBREW_GEMATRIA[current])
                current = ""
                i += 1
            else:
                if current in HEBREW_GEMATRIA:
                    letters.append(HEBREW_GEMATRIA[current])
                current = ""

        i += 1

    if current and current in HEBREW_GEMATRIA:
        letters.append(HEBREW_GEMATRIA[current])

    return "".join(letters)


def clean_html(text: str) -> str:
    return BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)


def lookup_jastrow(word: str) -> str:
    word = word.strip()

    if not word:
        return "No word detected."

    try:
        completion_url = (
            f"https://www.sefaria.org/api/words/completion/"
            f"{quote(word)}/{quote(LEXICON)}"
        )
        completion_response = requests.get(completion_url, timeout=10)
        completion_response.raise_for_status()
        matches = completion_response.json()

        print("SEFARIA MATCHES:", matches)

        if not matches:
            return f"No Jastrow result found for: {word}"

        first_match = matches[0]

        if isinstance(first_match, list):
            headword = first_match[0]
        else:
            headword = str(first_match)

        entry_url = f"https://www.sefaria.org/api/words?lookup_ref={quote(headword)}"
        entry_response = requests.get(entry_url, timeout=10)
        entry_response.raise_for_status()
        entry_data = entry_response.json()

        print("ENTRY DATA:", entry_data)

        items = entry_data.get("items", [])

        if not items:
            return f"Found {headword}, but no definition was available."

        entry = items[0]
        definition = ""

        if isinstance(entry, dict):
            content = entry.get("content", "")

            if isinstance(content, dict):
                definition = " ".join(clean_html(str(v)) for v in content.values())
            else:
                definition = clean_html(str(content))

        if not definition:
            definition = f"Found entry for {headword}, but definition unavailable."

        return f"{headword}. {definition[:900]}"

    except Exception as e:
        print(f"Sefaria lookup failed: {e}")
        return f"Lookup failed for: {word}"


def make_voice_text(text: str) -> str:
    text = text.replace("https://", " link omitted ")
    text = text.replace("http://", " link omitted ")
    return text[:900]


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
        input="dtmf",
        action="/voice-keypad-result",
        method="POST",
        timeout=10,
        num_digits=40,
        finish_on_key="#"
    )

    gather.say(
        "Enter the Hebrew word using gematria numbers. "
        "Use star between letters. "
        "Use star star after a number for a final letter. "
        "Press pound when done. "
        "For example, for Shabbos, enter 300 star 2 star 400 pound."
    )

    response.append(gather)

    response.say("I did not receive any digits. Please try again.")
    response.redirect("/voice")

    return str(response), 200, {"Content-Type": "application/xml"}


@app.route("/voice-keypad-result", methods=["POST"])
def voice_keypad_result():
    digits = request.form.get("Digits", "").strip()
    hebrew_word = keypad_to_hebrew(digits)

    print("DIGITS:", digits)
    print("PARSED WORD:", hebrew_word)

    response = VoiceResponse()

    if not hebrew_word:
        response.say("Sorry, I could not understand the keypad entry. Please try again.")
        response.redirect("/voice")
        return str(response), 200, {"Content-Type": "application/xml"}

    result = lookup_jastrow(hebrew_word)
    spoken_result = make_voice_text(result)

    response.say(f"You entered the word {hebrew_word}.")
    response.pause(length=1)
    response.say(spoken_result)
    response.pause(length=1)
    response.say("Goodbye.")

    return str(response), 200, {"Content-Type": "application/xml"}


@app.route("/test/<path:digits>", methods=["GET"])
def test_digits(digits):
    hebrew_word = keypad_to_hebrew(digits)
    result = lookup_jastrow(hebrew_word)

    return {
        "digits": digits,
        "parsed_hebrew_word": hebrew_word,
        "jastrow_result": result,
        "voice_text": make_voice_text(result),
    }


if __name__ == "__main__":
    app.run(debug=True)