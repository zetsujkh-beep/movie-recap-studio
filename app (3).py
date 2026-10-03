from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.exceptions import HTTPException
import os
import re
import time
import uuid
import asyncio
import tempfile
import edge_tts
from google import genai

app = Flask(__name__)

# =========================
# SETTINGS
# =========================

# Gemini model စာရင်း - env var GEMINI_MODELS နဲ့ ပြောင်းလို့ရတယ် (comma ခြား)
DEFAULT_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]
MODELS = [
    m.strip()
    for m in os.environ.get("GEMINI_MODELS", ",".join(DEFAULT_MODELS)).split(",")
    if m.strip()
]

MAX_TRANSCRIPT_CHARS = int(os.environ.get("MAX_TRANSCRIPT_CHARS", "60000"))
TTS_CHUNK_CHARS = 1500            # TTS ကို အပိုင်းလိုက်ထုတ်မယ့် အရှည်
FILE_MAX_AGE_SECONDS = 60 * 60    # ၁ နာရီကျော်တဲ့ ဖိုင်တွေ ဖျက်မယ်
SRT_MAX_CHARS = 80                # subtitle တစ်ကြောင်းအများဆုံး စာလုံးရေ
MIN_CUE_SECONDS = 0.4

# edge-tts က 24kHz / 48kbps mono mp3 ထုတ်တယ် -> 6000 bytes = 1 စက္ကန့်
BYTES_PER_SECOND = 6000

JOB_ID_RE = re.compile(r"[0-9a-f]{32}")
TEMP_DIR = tempfile.gettempdir()
SENTENCE_END = ("။", ".", "!", "?")


# =========================
# HOME
# =========================

@app.route("/")
def home():
    return render_template("index.html")


# =========================
# HELPERS
# =========================

def job_path(job_id, ext):
    return os.path.join(TEMP_DIR, f"movie_recap_{job_id}.{ext}")


def cleanup_old_files():
    now = time.time()
    try:
        for name in os.listdir(TEMP_DIR):
            if name.startswith("movie_recap_") and name.endswith((".mp3", ".srt")):
                path = os.path.join(TEMP_DIR, name)
                try:
                    if now - os.path.getmtime(path) > FILE_MAX_AGE_SECONDS:
                        os.remove(path)
                except OSError:
                    pass
    except OSError:
        pass


def split_sentences(text):
    # ။ ပြီးရင် space မလိုဘူး၊ . ! ? ပြီးရင်တော့ space လိုတယ် (3.5 လို ဂဏန်းမခွဲမိအောင်)
    parts = re.split(r"(?<=။)\s*|(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p and p.strip()]


# =========================
# CLEAN TRANSCRIPT
# =========================

def clean_transcript(text):
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\(.*?\)", "", text)

    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            lines.append(line)

    return " ".join(lines)


# =========================
# RECAP PROMPT
# =========================

def make_prompt(transcript, style):

    style_text = {
        "natural":
            "စကားပြောသလို သဘာဝကျပြီး နားထောင်လို့ကောင်းအောင် ရေးပါ။",

        "fast":
            "အရှိန်မြန်ပြီး စိတ်ဝင်စားစရာကောင်းအောင် ရေးပါ။ မလိုအပ်တဲ့အပိုင်းတွေကို ချုံ့ပါ။",

        "cinematic":
            "ရုပ်ရှင်ပြန်ပြောပြသလို cinematic feeling ရအောင် ရေးပါ။"
    }

    selected_style = style_text.get(style, style_text["natural"])

    return f"""
You are a professional movie recap writer.

Rewrite the following movie transcript into a Burmese
movie recap narration.

Rules:

- Write in natural spoken Burmese.
- Do not translate word-for-word.
- Keep the original story and events accurate.
- Remove unnecessary dialogue.
- Make the narration interesting.
- Do not invent events that are not in the transcript.
- Do not use section headings.
- Write continuously as a narration.
- {selected_style}

Transcript:

{transcript}
"""


# =========================
# GEMINI
# =========================

RETRY_MARKERS = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded")


def generate_ai_recap(transcript, style):

    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise Exception("GEMINI_API_KEY မထည့်ရသေးပါ")

    client = genai.Client(api_key=api_key)
    prompt = make_prompt(transcript, style)

    last_error = None

    for model in MODELS:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt
                )

                if response.text:
                    return response.text.strip()

                last_error = Exception(f"{model}: empty response")
                break

            except Exception as e:
                last_error = e
                error_text = str(e)

                # overload / rate limit ဆိုရင် ပြန်စမ်းမယ်
                if any(x in error_text for x in RETRY_MARKERS):
                    time.sleep(2 ** attempt)
                    continue

                # တခြား error (model မရှိ၊ key မှား...) ဆိုရင် နောက် model ကူးမယ်
                break

    raise Exception(
        "Gemini models temporarily unavailable. "
        f"Last error: {last_error}"
    )


# =========================
# TTS
# =========================

VOICE_MALE = "my-MM-ThihaNeural"
VOICE_FEMALE = "my-MM-NilarNeural"


def split_for_tts(text, max_chars=TTS_CHUNK_CHARS):
    sentences = split_sentences(text)

    chunks = []
    current = ""

    for sentence in sentences:
        # စာကြောင်းတစ်ကြောင်းတည်းက အရမ်းရှည်ရင် အတင်းဖြတ်မယ်
        while len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:max_chars])
            sentence = sentence[max_chars:]

        if current and len(current) + len(sentence) + 1 > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()

    if current:
        chunks.append(current)

    return chunks


def speed_to_rate(speed):
    try:
        speed_value = float(speed)
    except (TypeError, ValueError):
        speed_value = 1.0

    speed_value = min(max(speed_value, 0.5), 2.0)

    # 1.0x -> +0%
    percentage = round((speed_value - 1) * 100)

    return f"+{percentage}%" if percentage >= 0 else f"{percentage}%"


async def synthesize_chunk(text, voice_name, rate):
    """အသံ bytes နဲ့ boundary event တွေကို ပြန်ပေးတယ်။"""

    try:
        # edge-tts 7.x: sentence timing တောင်းမယ်
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice_name,
            rate=rate,
            boundary="SentenceBoundary"
        )
    except TypeError:
        # edge-tts အဟောင်း (boundary parameter မရှိ)
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice_name,
            rate=rate
        )

    audio = bytearray()
    events = []

    async for message in communicate.stream():
        kind = message.get("type")

        if kind == "audio":
            audio.extend(message["data"])
        elif kind in ("SentenceBoundary", "WordBoundary"):
            events.append(message)

    return bytes(audio), events


# =========================
# SUBTITLE TIMING
# =========================

def smart_join(tokens):
    """Word boundary စာလုံးတွေ ပေါင်းတာ - မြန်မာစာက space မလို၊ English စာလုံးကြားတော့ space ထည့်မယ်။"""
    result = ""
    for token in tokens:
        if (
            result
            and re.search(r"[A-Za-z0-9]$", result)
            and re.match(r"[A-Za-z0-9]", token)
        ):
            result += " "
        result += token
    return result


def cues_from_events(events, base):
    """edge-tts boundary event တွေကနေ (start, end, text) စာရင်းလုပ်တယ်။ မရရင် [] ပြန်မယ်။"""

    sentence_events = [e for e in events if e.get("type") == "SentenceBoundary"]

    cues = []

    if sentence_events:
        for e in sentence_events:
            text = str(e.get("text", "")).strip()
            if not text:
                continue
            start = base + e["offset"] / 1e7
            end = base + (e["offset"] + e["duration"]) / 1e7
            cues.append((start, end, text))
    else:
        word_events = [e for e in events if e.get("type") == "WordBoundary"]

        tokens = []
        start = None
        end = 0

        for e in word_events:
            if start is None:
                start = e["offset"]
            tokens.append(str(e.get("text", "")))
            end = e["offset"] + e["duration"]

            joined = smart_join(tokens).strip()

            if joined.endswith(SENTENCE_END):
                cues.append((base + start / 1e7, base + end / 1e7, joined))
                tokens = []
                start = None

        if tokens:
            joined = smart_join(tokens).strip()
            if joined:
                cues.append((base + start / 1e7, base + end / 1e7, joined))

    # timing တန်ဖိုးတွေ အားလုံး 0 ဖြစ်နေရင် (service က မပေးရင်) သုံးလို့မရဘူး
    if cues and (cues[-1][1] - cues[0][0]) < 0.5:
        return []

    return cues


def cues_proportional(chunk_text, base, duration):
    """Boundary မရတဲ့အခါ - အသံဖိုင်ရဲ့ တကယ့်အရှည်ကို စာလုံးရေအလိုက် ခွဲပေးတယ်။"""

    sentences = split_sentences(chunk_text)

    if not sentences:
        return []

    total_chars = sum(len(s) for s in sentences) or 1

    cues = []
    cursor = base

    for sentence in sentences:
        share = duration * (len(sentence) / total_chars)
        cues.append((cursor, cursor + share, sentence))
        cursor += share

    return cues


def split_long_cue(start, end, text, max_chars=SRT_MAX_CHARS):
    """စာရှည်လွန်တဲ့ subtitle ကို space / ၊ မှာ ဖြတ်ပြီး အချိန်ကို စာလုံးရေအလိုက် ခွဲမယ်။"""

    if len(text) <= max_chars:
        return [(start, end, text)]

    pieces = [p for p in re.split(r"(?<=၊)\s*|\s+", text) if p]

    lines = []
    current = ""

    for piece in pieces:
        if current and len(current) + len(piece) + 1 > max_chars:
            lines.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()

    if current:
        lines.append(current)

    if len(lines) <= 1:
        return [(start, end, text)]

    total_chars = sum(len(l) for l in lines) or 1
    duration = end - start

    result = []
    cursor = start

    for line in lines:
        share = duration * (len(line) / total_chars)
        result.append((cursor, cursor + share, line))
        cursor += share

    return result


def finalize_cues(cues):
    """စာရှည်ဖြတ်၊ overlap ဖယ်၊ အနည်းဆုံးကြာချိန်ညှိ။"""

    expanded = []
    for start, end, text in cues:
        expanded.extend(split_long_cue(start, end, text))

    expanded.sort(key=lambda c: c[0])

    fixed = []

    for i, (start, end, text) in enumerate(expanded):
        if end - start < MIN_CUE_SECONDS:
            end = start + MIN_CUE_SECONDS

        # နောက် cue နဲ့ မထပ်စေနဲ့
        if i + 1 < len(expanded):
            next_start = expanded[i + 1][0]
            if end > next_start:
                end = max(start + 0.05, next_start)

        fixed.append((start, end, text))

    return fixed


def format_time(seconds):

    total_ms = max(0, int(round(seconds * 1000)))

    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000

    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(cues, output_file):

    lines = []

    for index, (start, end, text) in enumerate(cues, 1):
        lines.append(str(index))
        lines.append(f"{format_time(start)} --> {format_time(end)}")
        lines.append(text)
        lines.append("")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# =========================
# VOICE + SRT (တစ်ခါတည်း)
# =========================

async def create_voice_and_cues(text, voice, speed, output_file):

    voice_name = VOICE_MALE if voice == "male" else VOICE_FEMALE
    rate = speed_to_rate(speed)

    all_cues = []
    base = 0.0

    with open(output_file, "wb") as f:

        for chunk_text in split_for_tts(text):

            audio, events = await synthesize_chunk(chunk_text, voice_name, rate)

            if not audio:
                raise Exception("TTS က အသံမပြန်ပေးပါ")

            f.write(audio)

            # အသံ အပိုင်းရဲ့ တကယ့်အရှည် (bytes ကနေ)
            chunk_seconds = len(audio) / BYTES_PER_SECOND

            cues = cues_from_events(events, base)

            if cues:
                # event တွေက ပိုရှည်နေရင် အဲဒါကို သုံးမယ်
                chunk_seconds = max(chunk_seconds, cues[-1][1] - base)
            else:
                cues = cues_proportional(chunk_text, base, chunk_seconds)

            all_cues.extend(cues)
            base += chunk_seconds

    return finalize_cues(all_cues)


# =========================
# RECAP API
# =========================

@app.route("/api/recap", methods=["POST"])
def recap():

    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "No data received"}), 400

    transcript = str(data.get("transcript", "")).strip()
    style = data.get("style", "natural")
    voice = data.get("voice", "male")
    speed = data.get("speed", "1.0")

    if not transcript:
        return jsonify({"error": "Transcript is empty"}), 400

    cleaned = clean_transcript(transcript)

    if not cleaned:
        return jsonify({"error": "Transcript is empty after cleaning"}), 400

    if len(cleaned) > MAX_TRANSCRIPT_CHARS:
        return jsonify({
            "error": f"Transcript too long (max {MAX_TRANSCRIPT_CHARS} characters)"
        }), 400

    cleanup_old_files()

    # AI recap

    try:
        script = generate_ai_recap(cleaned, style)
    except Exception as e:
        return jsonify({"error": "AI error: " + str(e)}), 502

    # Files (request တစ်ခုချင်းစီ သီးခြား)

    job_id = uuid.uuid4().hex
    mp3_file = job_path(job_id, "mp3")
    srt_file = job_path(job_id, "srt")

    # Voice + SRT

    try:
        cues = asyncio.run(
            create_voice_and_cues(script, voice, speed, mp3_file)
        )
        write_srt(cues, srt_file)

    except Exception as e:
        for f in (mp3_file, srt_file):
            if os.path.exists(f):
                os.remove(f)
        return jsonify({"error": "TTS error: " + str(e)}), 500

    response = jsonify({
        "script": script,
        "mp3": f"/api/download/mp3/{job_id}",
        "srt": f"/api/download/srt/{job_id}"
    })

    # index.html က /api/download/mp3 လို့ပဲ ခေါ်နေရင်လည်း အလုပ်လုပ်အောင်
    # browser တစ်ခုချင်းစီရဲ့ နောက်ဆုံး job ကို cookie နဲ့ မှတ်ထားတယ်
    response.set_cookie(
        "recap_job",
        job_id,
        max_age=FILE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="Lax"
    )

    return response


# =========================
# DOWNLOADS
# =========================

def send_job_file(job_id, ext, download_name, mimetype):

    if not job_id or not JOB_ID_RE.fullmatch(job_id):
        return "Invalid or missing id", 400

    file = job_path(job_id, ext)

    if not os.path.exists(file):
        return f"{ext.upper()} not found", 404

    return send_file(
        file,
        as_attachment=True,
        download_name=download_name,
        mimetype=mimetype
    )


@app.route("/api/download/mp3/<job_id>")
def download_mp3(job_id):
    return send_job_file(job_id, "mp3", "movie-recap.mp3", "audio/mpeg")


@app.route("/api/download/srt/<job_id>")
def download_srt(job_id):
    return send_job_file(job_id, "srt", "movie-recap.srt", "text/plain")


# အရင် URL အဟောင်းတွေ (index.html မပြင်ရသေးရင်) - cookie ထဲက job ကို သုံးမယ်
@app.route("/api/download/mp3")
def download_mp3_legacy():
    return send_job_file(
        request.cookies.get("recap_job"), "mp3", "movie-recap.mp3", "audio/mpeg"
    )


@app.route("/api/download/srt")
def download_srt_legacy():
    return send_job_file(
        request.cookies.get("recap_job"), "srt", "movie-recap.srt", "text/plain"
    )


# =========================
# HEALTH CHECK
# =========================

@app.route("/health")
def health():
    return {"status": "ok"}


# =========================
# ERROR HANDLER
# =========================

@app.errorhandler(Exception)
def handle_error(e):
    if isinstance(e, HTTPException):
        return jsonify({"error": e.description}), e.code
    return jsonify({"error": str(e)}), 500


# =========================
# START
# =========================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
