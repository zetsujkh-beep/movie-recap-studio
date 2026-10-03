
# 🎬 Movie Recap Studio V1

Phone-friendly Flask web app.

## V1
- SRT/TXT upload
- SRT cleanup
- Burmese recap prompt generator
- Script editor
- Burmese male/female TTS
- Speed control
- MP3 download

## Deploy on Render
1. Create a GitHub repository.
2. Upload all files/folders from this project.
3. Render → New → Web Service.
4. Connect the GitHub repository.
5. Build command:
   pip install -r requirements.txt
6. Start command:
   gunicorn app:app
7. Plan: Free.
8. Deploy.

## Important
This version does not store uploaded files permanently.
Render Free services can spin down after inactivity and local filesystem data is ephemeral.

The TTS layer uses the `edge-tts` Python package and Microsoft neural voice IDs:
- my-MM-ThihaNeural
- my-MM-NilarNeural

For production/commercial use, verify the current service and voice terms before publishing generated audio.
