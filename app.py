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


def remove_citation_noise(text: str) -> str:
    """
    Aggressively removes Jastrow citation/reference clutter while trying
    to preserve short English definition phrases.
    """
    text = clean_html(text)

    # Remove Hebrew/Aramaic and Greek characters.
    text = re.sub(r"[\u0590-\u05FF]+", " ", text)
    text = re.sub(r"[\u0370-\u03FF]+", " ", text)

    # Remove parenthetical notes: (b. h.), (v. ...), (of scholars), etc.
    text = re.sub(r"\([^)]*\)", " ", text)

    # Remove leading sense numbers: 1), 2), etc.
    text = re.sub(r"^\s*\d+\)\s*", "", text)

    # Remove superscript letters and page markers: 65ᵇ, 39ᵃ, 7ᶜ, etc.
    text = re.sub(r"\b\d+[ᵃᵇᶜᵈ]\b", " ", text)
    text = re.sub(r"[ᵃᵇᶜᵈ]", " ", text)

    # Remove Roman numeral citations: VI, 25; III, 8; XXI, 19.
    text = re.sub(r"\b[IVXLCDM]+,\s*\d+\b", " ", text)

    # Remove standalone biblical-style references like Ex. XXI, 19.
    text = re.sub(r"\b[A-Z][a-z]{1,8}\.\s*[IVXLCDM]+,\s*\d+\b", " ", text)

    # Remove rabbinic source citations:
    # B. Kam. VIII, 1; Deut. R. s. 9; Lam. R. to V, 14; Y. Ber. IV, 7c.
    text = re.sub(r"\b[A-Z]\.\s*[A-Z][a-zA-Z.]*\.?\s+[IVXLCDM]+,\s*\d+\b", " ", text)
    text = re.sub(r"\b[A-Z][a-z]+\.?\s+R\.\s+(?:s\.|to)?\s*[IVXLCDM\d]+(?:,\s*\d+)?\b", " ", text)
    text = re.sub(r"\bY\.\s*[A-Z][a-zA-Z.]*\.?\s+[IVXLCDM]+,\s*\d+[a-zᵃᵇᶜᵈ]*\b", " ", text)

    # Remove generic tractate/source citations: Sabb. 118a, Zeb. I, 3, Tam. I, 4.
    text = re.sub(
        r"\b[A-Z][a-zA-Z.]*\.?\s+(?:[IVXLCDM]+|\d+)(?:,\s*\d+)?[a-zᵃᵇᶜᵈ]*\b",
        " ",
        text,
    )

    # Remove ibid/cross-reference fragments.
    text = re.sub(r"\bib\.?\s*\d*[a-zᵃᵇᶜᵈ]*", " ", text, flags=re.I)
    text = re.sub(r"\b(?:v|vid|vide)\.?\s+[A-Za-z\u0590-\u05FF]+", " ", text, flags=re.I)
    text = re.sub(r"\b(?:v|vid|vide)\.?\b", " ", text, flags=re.I)

    # Remove common editorial/Jastrow abbreviations.
    abbreviations = [
        "Inf", "Part", "Pl", "Sing", "cmp", "opp", "denom", "trnsf",
        "transf", "esp", "ed", "Ms", "Ar", "ch", "b. h", "a. fr",
        "a. e", "bot", "top", "l. c", "oth", "preced", "same",
        "supra", "infra",
    ]
    for abbr in abbreviations:
        pattern = r"\b" + re.escape(abbr) + r"\.?\b"
        text = re.sub(pattern, " ", text, flags=re.I)

    # Remove "&c.", "etc.", and similar tails.
    text = re.sub(r"&c\.?|etc\.?", " ", text, flags=re.I)

    # Remove common quoted/example lead-ins.
    text = re.sub(r"\b(read|ref\.?|expl\.?|opp\.?|Ms\.?|ed\.?)\b.*$", " ", text, flags=re.I)

    # Remove known example-style phrases that are not definitions.
    example_patterns = [
        r"thou didst meet.*$",
        r"k[’']far.*$",
        r"the thoroughly lighted coals.*$",
        r"the informer[’']s bread.*$",
        r"where the transient poor.*$",
        r"if a person goes away.*$",
        r"the place of the throne.*$",
        r"so that the king.*$",
        r"to estimate indemnity.*$",
        r"whenever one is bound.*$",
        r"there are two kinds.*$",
        r"this refers to.*$",
        r"a gentile that.*$",
        r"rest like the Lord.*$",
        r"he who forswears.*$",
        r"a light which.*$",
        r"a kind of salt.*$",
        r"once the disciples.*$",
        r"and who lectured.*$",
        r"is it possible.*$",
        r"thou mayest.*$",
        r"went down to.*$",
        r"went up and.*$",
        r"the laws concerning.*$",
        r"I have a precious.*$",
        r"we Jews have.*$",
        r"one must break.*$",
        r"one should always.*$",
        r"come ye.*$",
        r"two ministering angels.*$",
        r"Jerusalem was destroyed.*$",
        r"which falls on.*$",
        r"whose Sabbath was it.*$",
        r"if Israel were.*$",
        r"if one says.*$",
        r"when do you find.*$",
        r"during those.*$",
        r"name of a treatise.*$",
    ]

    for pattern in example_patterns:
        text = re.sub(pattern, " ", text, flags=re.I)

    # Clean punctuation and spacing.
    text = text.replace("—", ";")
    text = text.replace("–", ";")
    text = text.replace(" .", ".")
    text = text.replace(" ,", ",")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*;\s*", "; ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = text.strip(" ;,.-")

    return text


def split_definition_into_phrases(definition: str) -> list[str]:
    """
    Split cleaned definition text into short phrases and discard anything
    that still looks like citation/reference/example noise.
    """
    text = remove_citation_noise(definition)

    # Split at semicolons first.
    raw_parts = text.split(";")
    pieces = []

    for part in raw_parts:
        part = part.strip(" ;,.-")
        if not part:
            continue

        lower = part.lower()

        # More cleanup after splitting.
        part = re.sub(r"^\s*(c|f|m|ch|same|preced|root|√)\.?\s+", "", part, flags=re.I)
        part = re.sub(r"\b(in|a|fr|ib|v)\.?\b", " ", part, flags=re.I)
        part = re.sub(r"\s+", " ", part).strip(" ;,.-")
        lower = part.lower()

        if not part:
            continue

        # Drop fragments that are almost certainly citations or residue.
        meaningless = {
            "in", "a", "v", "ib", "fr", "c", "d", "e", "bot", "top",
            "differ of opin", "differ. of opin",
        }

        if lower in meaningless:
            continue

        if re.search(r"\b[IVXLCDM]+,\s*\d+\b", part):
            continue

        if re.search(r"\b\d+[ᵃᵇᶜᵈ]\b", part):
            continue

        if re.search(r"\b[A-Z][a-z]+\.?\s+[IVXLCDM\d]", part):
            continue

        if re.search(r"\b(?:deut|sabb|zeb|tam|dan|ber|yeb|keth|gitt|ned|pes|men|peah|erub|sifra|targ|yalk)\b", lower):
            continue

        if re.search(r"\b(the|thou|where|when|who|which|if|ib|ms|ed)\b", lower) and not lower.startswith("to "):
            # Most of these are example sentences, not definitions.
            continue

        if len(part.split()) > 10:
            continue

        # Manual refinements for common compact definitions.
        if lower == "day of rest, sabbath":
            pieces.extend(["day of rest", "Sabbath"])
            continue

        if lower == "to cause to cease, remove":
            pieces.extend(["to cause to cease", "to remove"])
            continue

        if lower == "to rest, cease":
            pieces.append("to rest, cease")
            continue

        # Prefer short definition-like phrases.
        if (
            lower.startswith("to ")
            or lower.startswith("a ")
            or lower.startswith("an ")
            or lower.startswith("the ")
            or "," in part
            or len(part.split()) <= 4
        ):
            pieces.append(part)

    return pieces


def extract_definition_phrases(obj):
    definitions = []

    if isinstance(obj, dict):
        if "definition" in obj:
            phrases = split_definition_into_phrases(str(obj["definition"]))
            definitions.extend(phrases)

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
        item = item.strip(" ;,.-")
        item = re.sub(r"\s+", " ", item)
        key = item.lower()

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