from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import re

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
    """
    Rules:
      *  = separator between letters
      ** = make previous letter final
      #  = finish key

    Examples:
      300*2*400# -> שבת
      300*40**#  -> שם
    """
    digits = digits.strip().replace("#", "")

    letters = []
    current = ""
    i = 0

    while i < len(digits):
        ch = digits[i]

        if ch.isdigit():
            current += ch

        elif ch == "*":
            # ** means make previous letter final
            if i + 1 < len(digits) and digits[i + 1] == "*":
                if current in FINAL_FORMS:
                    letters.append(FINAL_FORMS[current])
                elif current in HEBREW_GEMATRIA:
                    letters.append(HEBREW_GEMATRIA[current])
                current = ""
                i += 1

            # single * means normal separator
            else:
                if current in HEBREW_GEMATRIA:
                    letters.append(HEBREW_GEMATRIA[current])
                current = ""

        i += 1

    if current and current in HEBREW_GEMATRIA:
        letters.append(HEBREW_GEMATRIA[current])

    return "".join(letters)


def clean_html(text: str) -> str:
    text = BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_definition(definition: str) -> str:
    definition = clean_html(definition)

    # Remove leading numbering like "1)", "2)", etc.
    definition = re.sub(r"^\s*\d+\)\s*", "", definition)

    # Remove parenthetical notes like "(b. h.)", "(v. ...)", "(of scholars)"
    definition = re.sub(r"\([^)]*\)", "", definition)

    # Remove some common leading Jastrow labels
    definition = re.sub(
        r"^\s*(c\.|f\.|m\.|ch\.|same|preced\.|b\. h\.)\s*",
        "",
        definition,
        flags=re.I,
    )

    # Cut off examples/source references. Everything after these usually
    # belongs to citations/examples, not the short definition.
    source_markers = [
        "Ukts.", "Ber.", "Maasr.", "Esth.", "Nidd.", "B. Kam.", "Gitt.",
        "Lam.", "Snh.", "Y.", "Sabb.", "Ned.", "Pes.", "Men.", "Peah",
        "B. Bath.", "Mekh.", "Erub.", "Sifra", "Yalk.", "Targ.",
        "R. Hash.", "Yeb.", "Keth.", "Is.", "Pesik.", "Ib.", "Ms.",
    ]

    for marker in source_markers:
        idx = definition.find(marker)
        if idx != -1:
            definition = definition[:idx]

    # Remove Hebrew/Aramaic text that sometimes survives inside definitions
    definition = re.sub(r"[\u0590-\u05FF]+", "", definition)

    # Normalize abbreviations
    definition = definition.replace("esp.", "especially")
    definition = definition.replace("b. h.", "")
    definition = definition.replace("ch.", "")

    definition = re.sub(r"\s+", " ", definition)
    definition = definition.strip(" ;,.-—")

    return definition


def split_definition_into_phrases(definition: str) -> list[str]:
    """
    Turns one cleaned Jastrow definition into short speakable phrases.

    Example:
      "to stay over the Sabbath; to deliver the Sabbath lecture"
    becomes:
      ["to stay over the Sabbath", "to deliver the Sabbath lecture"]
    """
    pieces = []

    for part in definition.split(";"):
        part = part.strip(" ;,.-—")
        if not part:
            continue

        # Remove long explanatory tails
        part = re.sub(r"\bas the center.*$", "", part).strip(" ;,.-")
        part = re.sub(r"\ballowed to rest, abandoned.*$", "allowed to rest", part).strip(" ;,.-")

        # "especially to observe..." -> "to observe..."
        part = re.sub(r"^especially\s+", "", part, flags=re.I)

        # Manual cleanup for common compact definitions
        lower = part.lower()

        if lower == "day of rest, sabbath":
            pieces.extend(["day of rest", "Sabbath"])
            continue

        if lower == "to cause to cease, remove":
            pieces.extend(["to cause to cease", "to remove"])
            continue

        if lower == "to rest; to observe the sabbath":
            pieces.extend(["to rest", "to observe the Sabbath"])
            continue

        pieces.append(part)

    return pieces


def extract_definition_phrases(obj):
    """
    Recursively extract only short definition phrases from Sefaria's
    nested Jastrow lexicon object.

    This avoids examples, source references, and long citation text.
    """
    definitions = []

    if isinstance(obj, dict):
        if "definition" in obj:
            cleaned = clean_definition(str(obj["definition"]))

            if cleaned:
                definitions.extend(split_definition_into_phrases(cleaned))

        for value in obj.values():
            definitions.extend(extract_definition_phrases(value))

    elif isinstance(obj, list):
        for item in obj:
            definitions.extend(extract_definition_phrases(item))

    return definitions


def dedupe_preserve_order(items):
    seen = set()
    result = []

    for item in items:
        item = item.strip(" ;,.-—")
        key = item.lower().strip()

        if key and key not in seen:
            seen.add(key)
            result.append(item)

    return result


def lookup_jastrow(word: str) -> str:
    word = word.strip()

    if not word:
        return "No word detected."

    try:
        url = f"https://www.sefaria.org/api/words/{quote(word)}"

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        entries = response.json()

        print("WORD LOOKUP ENTRIES:", entries)

        if not entries:
            return f"No Jastrow result found for: {word}"

        jastrow_entries = [
            entry for entry in entries
            if isinstance(entry, dict)
            and entry.get("parent_lexicon") == LEXICON
        ]

        if not jastrow_entries:
            return f"No Jastrow result found for: {word}"

        all_definitions = []

        for entry in jastrow_entries:
            content = entry.get("content", {})
            definitions = extract_definition_phrases(content)
            all_definitions.extend(definitions)

        all_definitions = dedupe_preserve_order(all_definitions)

        if not all_definitions:
            return f"Found {word}, but no readable definitions were available."

        # Keep voice output manageable
        all_definitions = all_definitions[:30]

        return "Definitions: " + "; ".join(all_definitions)

    except Exception as e:
        print(f"Sefaria lookup failed: {e}")
        return f"Lookup failed for: {word}"


def make_voice_text(text: str) -> str:
    text = text.replace("https://", " link omitted ")
    text = text.replace("http://", " link omitted ")
    text = re.sub(r"\s+", " ", text)
    return text[:1300]


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
        finish_on_key="#",
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