from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
import requests
from urllib.parse import quote

app = Flask(__name__)

LEXICON = "Jastrow Dictionary"

HEBREW_GEMATRIA = {
    "1": "א",
    "2": "ב",
    "3": "ג",
    "4": "ד",
    "5": "ה",
    "6": "ו",
    "7": "ז",
    "8": "ח",
    "9": "ט",
    "10": "י",
    "20": "כ",
    "30": "ל",
    "40": "מ",
    "50": "נ",
    "60": "ס",
    "70": "ע",
    "80": "פ",
    "90": "צ",
    "100": "ק",
    "200": "ר",
    "300": "ש",
    "400": "ת",
}

FINAL_FORMS = {
    "20": "ך",
    "40": "ם",
    "50": "ן",
    "80": "ף",
    "90": "ץ",
}


def keypad_to_hebrew(digits: str) -> str:
    """
    Rules:
      #  = separator between letters
      ## = end of word
      *  = make previous letter final

    Examples:
      300#2#400## -> שבת
      300#40*##   -> שם
    """

    digits = digits.strip()

    letters = []
    current = ""
    i = 0

    while i < len(digits):
        ch = digits[i]

        if ch.isdigit():
            current += ch

        elif ch == "*":
            if current in FINAL_FORMS:
                letters.append(FINAL_FORMS[current])
            elif current in HEBREW_GEMATRIA:
                letters.append(HEBREW_GEMATRIA[current])

            current = ""

        elif ch == "#":

            # detect ##
            if i + 1 < len(digits) and digits[i + 1] == "#":

                if current in HEBREW_GEMATRIA:
                    letters.append(HEBREW_GEMATRIA[current])

                current = ""   # prevents duplicate last letter
                break

            else:
                if current in HEBREW_GEMATRIA:
                    letters.append(HEBREW_GEMATRIA[current])

                current = ""

        i += 1

    if current and current in HEBREW_GEMATRIA:
        letters.append(HEBREW_GEMATRIA[current])

    return "".join(letters)


def lookup_jastrow(word: str) -> str:
    word = word.strip()

    if not word:
        return "No word detected."

    try:
        url = (
            f"https://www.sefaria.org/api/words/completion/"
            f"{quote(word)}/{quote(LEXICON)}"
        )

        response = requests.get(url, timeout=10)
        response.raise_for_status()

        matches = response.json()

        print("SEFARIA MATCHES:", matches)

    except Exception as e:
        print(f"Sefaria lookup failed: {e}")
        return f"Lookup failed for: {word}"

    if not matches:
        return f"No Jastrow result found for: {word}"

    lines = []

    for match in matches[:3]:

        if isinstance(match, list):
            plain = match[0]
            pointed = match[1] if len(match) > 1 else plain
        else:
            plain = str(match)
            pointed = plain

        lines.append(pointed)

    return "Top Jastrow matches:\n\n" + "\n\n".join(lines)


@app.route("/", methods=["GET"])
def home():
    return "Jastrow SMS/Voice app is running."


@app.route("/sms", methods=["POST"])
def sms():

    incoming = request.form.get("Body", "").strip()

    response = MessagingResponse()

    result = lookup_jastrow(incoming)

    response.message(result[:1500])

    return str(response), 200, {"Content-Type": "application/xml"}


@app.route("/voice", methods=["POST"])
def voice():

    response = VoiceResponse()

    gather = Gather(
        input="dtmf",
        action="/voice-keypad-result",
        method="POST",
        timeout=10,
        num_digits=40
    )

    gather.say(
        "Enter the Hebrew word using gematria numbers. "
        "Use pound between letters. "
        "Use star after a number for a final letter. "
        "Use pound pound to end the word. "
        "For example, for Shabbos, enter "
        "300 pound 2 pound 400 pound pound."
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

        response.say(
            "Sorry, I could not understand the keypad entry. "
            "Please try again."
        )

        response.redirect("/voice")

        return str(response), 200, {"Content-Type": "application/xml"}

    result = lookup_jastrow(hebrew_word)

    spoken_result = result[:1000]

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
        "jastrow_result": result
    }


if __name__ == "__main__":
    app.run(debug=True)