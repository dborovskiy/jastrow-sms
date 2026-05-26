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
      * = separator between letters
      # = finish key

    Last letter automatically uses final form when available.

    Examples:
      300*2*400# -> שבת
      300*40#    -> שם
      1*20*30#   -> אכל
      80*3*300#  -> פגש
    """
    digits = digits.strip().replace("#", "")
    parts = [part for part in digits.split("*") if part]

    letters = []

    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1

        if is_last and part in FINAL_FORMS:
            letters.append(FINAL_FORMS[part])
        elif part in HEBREW_GEMATRIA:
            letters.append(HEBREW_GEMATRIA[part])

    return "".join(letters)


def extract_italic_text_from_definition(html: str) -> list[str]:
    """
    Extract italic text only when it is:
      1. the first meaningful text in the definition, or
      2. the first italic text after a numbered marker like 1), 2), 3)

    No extra definition-cleaning is applied.
    """
    soup = BeautifulSoup(html or "", "html.parser")

    results = []
    seen_meaningful_text = False
    allow_next_italic_after_number = False

    def walk(node):
        nonlocal seen_meaningful_text, allow_next_italic_after_number

        for child in getattr(node, "children", []):
            # Italic node
            if getattr(child, "name", None) in ["i", "em"]:
                italic_text = child.get_text(" ", strip=True)

                if not italic_text:
                    continue

                if not seen_meaningful_text:
                    results.append(italic_text)
                    seen_meaningful_text = True
                    allow_next_italic_after_number = False

                elif allow_next_italic_after_number:
                    results.append(italic_text)
                    seen_meaningful_text = True
                    allow_next_italic_after_number = False

                else:
                    # Italic text appears later in an example/citation;
                    # intentionally ignore it.
                    seen_meaningful_text = True

            # Other HTML tag
            elif getattr(child, "name", None) is not None:
                walk(child)

            # Plain text node
            else:
                text = str(child).strip()

                if not text:
                    continue

                # If text is only a numbered marker like 1), 2), 3),
                # then the next italic phrase is allowed.
                if re.fullmatch(r"\d+\)", text):
                    allow_next_italic_after_number = True
                    seen_meaningful_text = True
                    continue

                # If text ends with a numbered marker, e.g. "... 1)"
                if re.search(r"\d+\)\s*$", text):
                    allow_next_italic_after_number = True
                    seen_meaningful_text = True
                    continue

                # Otherwise this is ordinary non-italic text.
                seen_meaningful_text = True
                allow_next_italic_after_number = False

    walk(soup)

    return results


def extract_italic_text(obj):
    """
    Recursively extract only italic text that passes the rule:
      - first italic text in a definition, or
      - first italic text after 1), 2), etc.
    """
    results = []

    if isinstance(obj, dict):
        if "definition" in obj:
            html = str(obj["definition"])
            results.extend(extract_italic_text_from_definition(html))

        for value in obj.values():
            results.extend(extract_italic_text(value))

    elif isinstance(obj, list):
        for item in obj:
            results.extend(extract_italic_text(item))

    return results


def dedupe_preserve_order(items):
    seen = set()
    result = []

    for item in items:
        item = re.sub(r"\s+", " ", item).strip()
        key = item.lower()

        if item and key not in seen:
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

        if not entries:
            return f"No Jastrow result found for: {word}"

        jastrow_entries = [
            entry for entry in entries
            if isinstance(entry, dict)
            and entry.get("parent_lexicon") == LEXICON
        ]

        if not jastrow_entries:
            return f"No Jastrow result found for: {word}"

        all_italic_text = []

        for entry in jastrow_entries:
            content = entry.get("content", {})
            all_italic_text.extend(extract_italic_text(content))

        all_italic_text = dedupe_preserve_order(all_italic_text)

        if not all_italic_text:
            return f"Found {word}, but no rule-matching italicized text was available."

        return "Definitions: " + "; ".join(all_italic_text[:40])

    except Exception as e:
        print(f"Sefaria lookup failed: {e}")
        return f"Lookup failed for: {word}"


def make_voice_text(text: str) -> str:
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
        "Press pound when done. "
        "The last letter will automatically use its final form when available. "
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


@app.route("/debug/<path:digits>", methods=["GET"])
def debug_digits(digits):
    """
    Debug route to inspect raw definition HTML and rule-matching italic text.
    """
    hebrew_word = keypad_to_hebrew(digits)

    url = f"https://www.sefaria.org/api/words/{quote(hebrew_word)}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    entries = response.json()

    debug_results = []

    def walk_debug(obj):
        out = []

        if isinstance(obj, dict):
            if "definition" in obj:
                html = str(obj["definition"])
                soup = BeautifulSoup(html or "", "html.parser")

                out.append({
                    "raw_html": html,
                    "plain_text": soup.get_text(" ", strip=True),
                    "all_italic_text": [
                        tag.get_text(" ", strip=True)
                        for tag in soup.find_all(["i", "em"])
                    ],
                    "rule_matched_italic_text": extract_italic_text_from_definition(html),
                })

            for value in obj.values():
                out.extend(walk_debug(value))

        elif isinstance(obj, list):
            for item in obj:
                out.extend(walk_debug(item))

        return out

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        if entry.get("parent_lexicon") != LEXICON:
            continue

        debug_results.extend(walk_debug(entry.get("content", {})))

    return {
        "digits": digits,
        "parsed_hebrew_word": hebrew_word,
        "debug_results": debug_results,
    }


if __name__ == "__main__":
    app.run(debug=True)