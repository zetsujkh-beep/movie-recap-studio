from flask import Flask, render_template, request, jsonify, send_file
import os
import re
import asyncio
import edge_tts
import tempfile
from google import genai

app = Flask(__name__)


# =========================
# HOME
# =========================

@app.route("/")
def home():
    return render_template("index.html")


# =========================
# CLEAN TRANSCRIPT
# =========================

def clean_transcript(text):
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\(.*?\)', '', text)

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

    selected_style = style_text.get(
        style,
        style_text["natural"]
    )

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

def generate_ai_recap(transcript, style):

    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise Exception(
            "GEMINI_API_KEY မထည့်ရသေးပါ"
        )

    client = genai.Client(
        api_key=api_key
    )

    prompt = make_prompt(
        transcript,
        style
    )

    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt
    )

    return response.text.strip()

# =========================
# TTS
# =========================

VOICE_MALE = "my-MM-ThihaNeural"
VOICE_FEMALE = "my-MM-NilarNeural"


async def create_voice(text, voice, speed, output_file):

    selected_voice = (
        VOICE_MALE
        if voice == "male"
        else VOICE_FEMALE
    )

    speed_value = float(speed)

    # Convert 1.0x → +0%
    percentage = int((speed_value - 1) * 100)

    if percentage >= 0:
        rate = f"+{percentage}%"
    else:
        rate = f"{percentage}%"

    communicate = edge_tts.Communicate(
        text=text,
        voice=selected_voice,
        rate=rate
    )

    await communicate.save(output_file)


# =========================
# CREATE SIMPLE SRT
# =========================

def create_srt(text, output_file):

    sentences = re.split(
        r'(?<=[။.!?])\s+',
        text.strip()
    )

    sentences = [
        x.strip()
        for x in sentences
        if x.strip()
    ]

    current_time = 0
    srt_lines = []

    for index, sentence in enumerate(sentences, 1):

        # Rough estimate:
        # Burmese narration ~ 4 chars / second
        duration = max(
            2,
            round(len(sentence) / 4)
        )

        start = current_time
        end = current_time + duration

        srt_lines.append(
            str(index)
        )

        srt_lines.append(
            f"{format_time(start)} --> "
            f"{format_time(end)}"
        )

        srt_lines.append(sentence)
        srt_lines.append("")

        current_time = end

    with open(
        output_file,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "\n".join(srt_lines)
        )


def format_time(seconds):

    hours = int(seconds // 3600)

    minutes = int(
        (seconds % 3600) // 60
    )

    secs = int(seconds % 60)

    milliseconds = 0

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{milliseconds:03d}"
    )


# =========================
# RECAP API
# =========================

@app.route("/api/recap", methods=["POST"])
def recap():

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "No data received"
        }), 400

    transcript = data.get(
        "transcript",
        ""
    ).strip()

    style = data.get(
        "style",
        "natural"
    )

    voice = data.get(
        "voice",
        "male"
    )

    speed = data.get(
        "speed",
        "1.0"
    )

    if not transcript:

        return jsonify({
            "error": "Transcript is empty"
        }), 400


    # Clean transcript

    cleaned = clean_transcript(
        transcript
    )


    # --------------------------------
    # TEMPORARY TEST
    # --------------------------------
    #
    # AI API မချိတ်ရသေးတဲ့အတွက်
    # အခု transcript ကို test အနေနဲ့
    # script အဖြစ်ပြန်သုံးထားပါတယ်။
    #
    # နောက်အဆင့်မှာ ဒီနေရာကို
    # AI API နဲ့ အစားထိုးမယ်.
    #

    script = generate_ai_recap(
    cleaned,
    style
    )


    # Temporary directory

    temp_dir = tempfile.gettempdir()

    mp3_file = os.path.join(
        temp_dir,
        "movie_recap.mp3"
    )

    srt_file = os.path.join(
        temp_dir,
        "movie_recap.srt"
    )


    # Generate voice

    try:

        asyncio.run(
            create_voice(
                script,
                voice,
                speed,
                mp3_file
            )
        )

    except Exception as e:

        return jsonify({
            "error":
                "TTS error: " + str(e)
        }), 500


    # Generate SRT

    create_srt(
        script,
        srt_file
    )


    return jsonify({

        "script": script,

        "mp3":
            "/api/download/mp3",

        "srt":
            "/api/download/srt"

    })


# =========================
# DOWNLOAD MP3
# =========================

@app.route("/api/download/mp3")
def download_mp3():

    file = os.path.join(
        tempfile.gettempdir(),
        "movie_recap.mp3"
    )

    if not os.path.exists(file):

        return "MP3 not found", 404

    return send_file(
        file,
        as_attachment=True,
        download_name="movie-recap.mp3",
        mimetype="audio/mpeg"
    )


# =========================
# DOWNLOAD SRT
# =========================

@app.route("/api/download/srt")
def download_srt():

    file = os.path.join(
        tempfile.gettempdir(),
        "movie_recap.srt"
    )

    if not os.path.exists(file):

        return "SRT not found", 404

    return send_file(
        file,
        as_attachment=True,
        download_name="movie-recap.srt",
        mimetype="text/plain"
    )


# =========================
# HEALTH CHECK
# =========================

@app.route("/health")
def health():

    return {
        "status": "ok"
    }

@app.errorhandler(Exception)
def handle_error(e):
    return jsonify({
        "error": str(e)
    }), 500

# =========================
# START
# =========================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                5000
            )
        )
    )
