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

    Final form rule:
      If the last letter has a final form, use the final form automatically.

    Examples:
      300*2*400# -> שבת
      300*40#    -> שם
      40*30*20#  -> מלך
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

    # Remove Hebrew/Aramaic text
    definition = re.sub(r"[\u0590-\u05FF]+", "", definition)

    # Remove superscript citation markers like 65ᵇ, 39ᵃ
    definition = re.sub(r"\b\d+[ᵃᵇᶜᵈ]\b", "", definition)

    # Remove Roman numeral citations like VI, 25
    definition = re.sub(r"\b[IVXLCDM]+,\s*\d+\b", "", definition)

    # Remove ibid fragments like ib. 65ᵇ
    definition = re.sub(r"\bib\.?\s*\d*[a-zᵃᵇᶜᵈ]*", "", definition, flags=re.I)

    # Remove Midrash-style citations like Deut. R. s. 9
    definition = re.sub(r"\b[A-Z][a-z]+\.\s+R\.\s+s\.\s*\d+\b", "", definition)

    # Remove "infra", "supra", etc.
    definition = re.sub(r"\b(infra|supra)\b", "", definition, flags=re.I)

    # Remove Jastrow/editorial abbreviations
    junk_words = [
        r"\bInf\.",
        r"\bPart\.",
        r"\btrnsf\.",
        r"\bdenom\.",
        r"\bcmp\.",
        r"\bopp\.",
        r"\bfr\.",
        r"\ba\. fr\.",
        r"\ba\. e\.",
        r"\bv\.",
        r"&c\.",
    ]

    for junk in junk_words:
        definition = re.sub(junk, "", definition, flags=re.I)

    # Remove common leading labels
    definition = re.sub(
        r"^\s*(c\.|f\.|m\.|ch\.|same|preced\.|b\. h\.|√)\s*",
        "",
        definition,
        flags=re.I,
    )

    # Remove source citations like:
    # Zeb. I, 3
    # Tam. I, 4
    # Dan. III, 8
    # Sabb. 118a
    # B. Kam. VIII, 1
    definition = re.sub(
        r"\b[A-Z]\.\s*[A-Z][a-zA-Z.]*\.?\s+[IVXLCDM]+,\s*\d+",
        "",
        definition,
    )

    definition = re.sub(
        r"\b[A-Z][a-zA-Z.]*\.?\s+"
        r"(?:[IVXLCDM]+|\d+)"
        r"(?:,\s*\d+)?"
        r"[a-zᵃᵇᶜᵈ]*",
        "",
        definition,
    )

    # Cut off after strong citation/example markers if any remain
    source_markers = [
        "Zeb.", "Tam.", "Dan.", "Deut. R.", "Ukts.", "Ber.", "Maasr.",
        "Esth.", "Nidd.", "B. Kam.", "Gitt.", "Lam.", "Snh.", "Y.",
        "Sabb.", "Ned.", "Pes.", "Men.", "Peah", "B. Bath.", "Mekh.",
        "Erub.", "Sifra", "Yalk.", "Targ.", "R. Hash.", "Yeb.", "Keth.",
        "Is.", "Pesik.", "Ib.", "Ms.",
    ]

    for marker in source_markers:
        idx = definition.find(marker)
        if idx != -1:
            definition = definition[:idx]

    definition = definition.replace("esp.", "especially")
    definition = definition.replace("b. h.", "")
    definition = definition.replace("ch.", "")
    definition = definition.replace(" ,", ",")
    definition = definition.replace(" .", ".")

    definition = re.sub(r"\s+", " ", definition)
    definition = definition.strip(" ;,.-—")

    return definition


def split_definition_into_phrases(definition: str) -> list[str]:
    """
    Turns cleaned Jastrow definition text into short speakable phrases.
    Removes source/example leftovers and keeps only definition-like phrases.
    """
    pieces = []

    for part in definition.split(";"):
        part = part.strip(" ;,.-—")
        if not part:
            continue

        # Remove superscript markers like 65ᵇ, 39ᵃ, 7ᶜ
        part = re.sub(r"\b\d+[ᵃᵇᶜᵈ]\b", "", part)

        # Remove Roman numeral citations like VI, 25 or III, 8
        part = re.sub(r"\b[IVXLCDM]+,\s*\d+\b", "", part)

        # Remove citation fragments like ib. 65b, Ib. 65ᵇ
        part = re.sub(r"\bib\.?\s*\d*[a-zᵃᵇᶜᵈ]*", "", part, flags=re.I)

        # Remove Midrash-style citations like Deut. R. s. 9
        part = re.sub(r"\b[A-Z][a-z]+\.\s+R\.\s+s\.\s*\d+\b", "", part)

        # Remove leftover citation fragments
        part = re.sub(r"\b[A-Z]\.\s*[A-Z][a-zA-Z.]*\.?\s+[IVXLCDM]+,\s*\d+", "", part)
        part = re.sub(r"\b[A-Z][a-zA-Z.]*\.?\s+[IVXLCDM]+,\s*\d+", "", part)

        # Remove editorial/source abbreviations
        part = re.sub(r"\bInf\.?\b", "", part, flags=re.I)
        part = re.sub(r"\bPart\.?\b", "", part, flags=re.I)
        part = re.sub(r"\btrnsf\.?\b", "", part, flags=re.I)
        part = re.sub(r"\bdiffer\. of opin\.?", "", part, flags=re.I)
        part = re.sub(r"\bwith prop\.?", "", part, flags=re.I)
        part = re.sub(r"\b(infra|supra)\b", "", part, flags=re.I)
        part = re.sub(r"&c\.?", "", part, flags=re.I)

        # Remove phrases that are clearly examples, not definitions
        bad_markers = [
            "the thoroughly lighted coals",
            "the informer’s bread",
            "the informer's bread",
            "thou didst meet the angel",
            "k’far",
            "k'far",
            "paggash",
            "hence",
            "Ms.",
            "ed.",
            "opp.",
        ]

        if any(marker.lower() in part.lower() for marker in bad_markers):
            continue

        # Remove common tails
        part = re.sub(r"\bas the center.*$", "", part).strip(" ;,.-")
        part = re.sub(r"\ballowed to rest, abandoned.*$", "allowed to rest", part).strip(" ;,.-")
        part = re.sub(r"^especially\s+", "", part, flags=re.I)

        # Clean punctuation/spaces
        part = part.replace(" ,", ",")
        part = part.replace(" .", ".")
        part = re.sub(r"\s+", " ", part)
        part = part.strip(" ;,.-—")

        lower = part.lower().strip()

        meaningless = {
            "in",
            "a",
            "v",
            "ib",
            "ib.",
            "c",
            "d",
            "e",
            "fr",
            "a fr",
            "a e",
            "infra",
            "supra",
        }

        if lower in meaningless:
            continue

        # Drop fragments that are just letters/numbers/punctuation
        if re.fullmatch(r"[a-zA-Zᵃᵇᶜᵈ0-9\s,.-]+", part) and len(part.split()) <= 2:
            if not part.lower().startswith("to "):
                continue

        # Manual cleanup for common compact definitions
        if lower == "day of rest, sabbath":
            pieces.extend(["day of rest", "Sabbath"])
            continue

        if lower == "to cause to cease, remove":
            pieces.extend(["to cause to cease", "to remove"])
            continue

        if lower == "to rest; to observe the sabbath":
            pieces.extend(["to rest", "to observe the Sabbath"])
            continue

        # Avoid very long example-like fragments
        if len(part.split()) > 12:
            continue

        pieces.append(part)

    return pieces


def extract_definition_phrases(obj):
    """
    Recursively extract only short definition phrases from Sefaria's
    nested Jastrow lexicon object.
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
        "Press pound when done. "
        "The last letter will automatically use its final form when available. "
        "For example, for Shabbos, enter 300 star 2 star 400 pound. "
        "For sham, enter 300 star 40 pound."
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