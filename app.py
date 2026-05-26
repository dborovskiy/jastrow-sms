from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import re

app = Flask(__name__)

LEXICON = "Jastrow Dictionary"
HEBREW_VOICE = "Google.he-IL-Standard-B"
HEBREW_LANGUAGE = "he-IL"

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


def normalize_for_dedupe(text: str) -> str:
    """
    Normalize aggressively for duplicate detection only.
    This does not change the text shown to the user.
    """
    text = re.sub(r"\s+", " ", text).strip().lower()

    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')

    text = re.sub(r"[.;:,\-—–]+", " ", text)
    text = re.sub(r"[\[\]{}()]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def split_definition_items(items: list[str]) -> list[str]:
    """
    Some italic strings may contain multiple definitions separated by semicolons.
    Split them before deduping.
    """
    split_items = []

    for item in items:
        item = re.sub(r"\s+", " ", item).strip()

        if not item:
            continue

        for piece in item.split(";"):
            piece = re.sub(r"\s+", " ", piece).strip(" ;,.-")

            if piece:
                split_items.append(piece)

    return split_items


def dedupe_preserve_order(items):
    seen = set()
    result = []

    for item in split_definition_items(items):
        key = normalize_for_dedupe(item)

        if key and key not in seen:
            seen.add(key)
            result.append(item)

    return result


def lookup_jastrow_data(word: str) -> dict:
    """
    Returns structured lookup data:

    {
        "ok": bool,
        "word": Hebrew query word,
        "result_keys": Hebrew/Aramaic headwords from Jastrow entries,
        "definitions": italic definitions,
        "error": optional error string
    }
    """
    word = word.strip()

    if not word:
        return {
            "ok": False,
            "word": word,
            "result_keys": [],
            "definitions": [],
            "error": "No word detected.",
        }

    try:
        url = f"https://www.sefaria.org/api/words/{quote(word)}"

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        entries = response.json()

        if not entries:
            return {
                "ok": False,
                "word": word,
                "result_keys": [],
                "definitions": [],
                "error": f"No Jastrow result found for: {word}",
            }

        jastrow_entries = [
            entry for entry in entries
            if isinstance(entry, dict)
            and entry.get("parent_lexicon") == LEXICON
        ]

        if not jastrow_entries:
            return {
                "ok": False,
                "word": word,
                "result_keys": [],
                "definitions": [],
                "error": f"No Jastrow result found for: {word}",
            }

        result_keys = []
        all_italic_text = []

        for entry in jastrow_entries:
            headword = entry.get("headword") or entry.get("word") or word
            if headword:
                result_keys.append(str(headword))

            content = entry.get("content", {})
            all_italic_text.extend(extract_italic_text(content))

        result_keys = dedupe_preserve_order(result_keys)
        all_italic_text = dedupe_preserve_order(all_italic_text)

        if not all_italic_text:
            return {
                "ok": False,
                "word": word,
                "result_keys": result_keys,
                "definitions": [],
                "error": f"Found {word}, but no rule-matching italicized text was available.",
            }

        return {
            "ok": True,
            "word": word,
            "result_keys": result_keys[:20],
            "definitions": all_italic_text[:40],
            "error": None,
        }

    except Exception as e:
        print(f"Sefaria lookup failed: {e}")
        return {
            "ok": False,
            "word": word,
            "result_keys": [],
            "definitions": [],
            "error": f"Lookup failed for: {word}",
        }


def lookup_jastrow(word: str) -> str:
    """
    Text/SMS-friendly formatted output.
    """
    data = lookup_jastrow_data(word)

    if not data["ok"]:
        return data["error"]

    keys_text = "; ".join(data["result_keys"])
    definitions_text = "; ".join(data["definitions"])

    if keys_text:
        return f"Definitions found for: {keys_text}\n\nDefinitions: {definitions_text}"

    return f"Definitions: {definitions_text}"


def make_voice_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text[:1300]


def say_hebrew(response: VoiceResponse, text: str):
    """
    Say text using the requested Hebrew Google voice.
    """
    response.say(
        text,
        voice=HEBREW_VOICE,
        language=HEBREW_LANGUAGE,
    )


def say_chunks(response: VoiceResponse, text: str, chunk_size: int = 900):
    """
    Twilio <Say> is easier to manage if long text is split into chunks.
    """
    text = make_voice_text(text)

    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size].strip()

        if chunk:
            say_hebrew(response, chunk)
            response.pause(length=1)


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

    data = lookup_jastrow_data(hebrew_word)

    if not data["ok"]:
        say_hebrew(response, data["error"])
        response.pause(length=1)
        say_hebrew(response, "Goodbye.")
        return str(response), 200, {"Content-Type": "application/xml"}

    result_keys = data["result_keys"]
    definitions = data["definitions"]

    say_hebrew(response, "Definitions found for")
    response.pause(length=1)

    for key in result_keys:
        say_hebrew(response, key)
        response.pause(length=1)

    say_hebrew(response, "The definitions are")
    response.pause(length=1)

    definitions_text = "; ".join(definitions)
    say_chunks(response, definitions_text)

    say_hebrew(response, "Goodbye.")

    return str(response), 200, {"Content-Type": "application/xml"}


@app.route("/test/<path:digits>", methods=["GET"])
def test_digits(digits):
    hebrew_word = keypad_to_hebrew(digits)
    data = lookup_jastrow_data(hebrew_word)

    return {
        "digits": digits,
        "parsed_hebrew_word": hebrew_word,
        "result_keys": data.get("result_keys", []),
        "definitions": data.get("definitions", []),
        "jastrow_result": lookup_jastrow(hebrew_word),
        "voice_text": make_voice_text("; ".join(data.get("definitions", []))),
        "ok": data.get("ok", False),
        "error": data.get("error"),
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