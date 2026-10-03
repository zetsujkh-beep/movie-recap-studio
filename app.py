
from flask import Flask, render_template, request, jsonify, send_file
import edge_tts
import asyncio
import os
import re
import tempfile
import uuid

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

VOICES = {
    "male": "my-MM-ThihaNeural",
    "female": "my-MM-NilarNeural",
}

def clean_srt(text):
    text = text.replace("\r", "")
    lines = text.split("\n")
    out = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if re.fullmatch(r"\d+", s):
            continue
        if re.match(r"^\d\d:\d\d:\d\d[,.]\d+\s+-->\s+\d\d:\d\d:\d\d[,.]\d+", s):
            continue
        s = re.sub(r"<[^>]+>", "", s)
        out.append(s)
    return "\n".join(out)

def build_recap_prompt(transcript):
    return f"""အောက်က Movie transcript/subtitle ကို Movie Recap အတွက် မြန်မာလို ပြန်ရေးပါ။

စည်းကမ်းများ:
- ဇာတ်လမ်းကို မပျက်စေဘဲ အဓိကဖြစ်ရပ်တွေကိုပဲ ရွေးပါ။
- မြန်မာစကားပြောဟန်နဲ့ သဘာဝကျကျရေးပါ။
- အစမှာ ကြည့်ချင်စေမယ့် Hook တစ်ကြောင်း ထည့်ပါ။
- ဇာတ်ကောင်တွေကို နားလည်လွယ်အောင် ရှင်းပြပါ။
- မလိုအပ်တဲ့ dialogue တွေ မထည့်ပါနဲ့။
- Scene အစဉ်မပျက်ထားပါ။
- Ending ကိုလည်း အတိုချုံးရှင်းပြပါ။
- English စကားလုံးတွေ မလိုအပ်ရင် မြန်မာလိုရေးပါ။
- Voice-over ဖတ်ရလွယ်အောင် စာကြောင်းတိုတိုရေးပါ။

TRANSCRIPT:
{transcript}
"""

async def make_tts(text, voice, rate, pitch, output):
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch
    )
    await communicate.save(output)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/srt", methods=["POST"])
def srt_api():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "SRT ဖိုင်ရွေးပါ"}), 400
    raw = f.read().decode("utf-8-sig", errors="ignore")
    cleaned = clean_srt(raw)
    return jsonify({
        "text": cleaned,
        "chars": len(cleaned),
        "prompt": build_recap_prompt(cleaned[:12000])
    })

@app.route("/api/tts", methods=["POST"])
def tts_api():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    gender = data.get("gender", "male")
    rate = data.get("rate", "+0%")
    pitch = data.get("pitch", "+0Hz")

    if not text:
        return jsonify({"error": "Script မရှိပါ"}), 400
    if len(text) > 5000:
        return jsonify({"error": "တစ်ခါလျှင် စာလုံး 5000 အထိပဲ ထည့်ပါ"}), 400

    rate = str(rate)
    pitch = str(pitch)
    if not re.fullmatch(r"[+-]\d{1,3}%", rate):
        rate = "+0%"
    if not re.fullmatch(r"[+-]\d{1,3}Hz", pitch):
        pitch = "+0Hz"

    filename = f"recap_{uuid.uuid4().hex}.mp3"
    path = os.path.join(tempfile.gettempdir(), filename)

    try:
        asyncio.run(make_tts(text, VOICES.get(gender, VOICES["male"]), rate, pitch, path))
        return send_file(path, mimetype="audio/mpeg", as_attachment=True, download_name="movie-recap-voice.mp3")
    except Exception as e:
        return jsonify({"error": "TTS ထုတ်ရာမှာ အမှားဖြစ်ပါတယ်", "detail": str(e)}), 500

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
