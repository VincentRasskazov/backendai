import os
import json
import time
import requests
import threading
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
# The password you must enter in the UI to use the API. 
# Change 'vincent-secret' to something harder if you want!
API_PASSWORD = os.environ.get('API_PASSWORD', 'vincent-secret')

# DeepAI Config
DEEPAI_URL = "https://api.deepai.org/hacking_is_a_serious_crime"
DEEPAI_KEY = "tryit-48957598737-7bf6498cad4adf00c76eb3dfa97dc26d"

# --- KEEP-ALIVE (Prevents Render Sleeping) ---
def keep_alive():
    time.sleep(10)
    app_url = os.environ.get('RENDER_EXTERNAL_URL')
    if app_url:
        print(f"Starting keep-alive for {app_url}")
        while True:
            try:
                time.sleep(800) # Ping every 13 mins
                requests.get(f"{app_url}/health")
            except:
                pass
threading.Thread(target=keep_alive, daemon=True).start()

# --- ROUTES ---

@app.route('/health', methods=['GET'])
def health():
    return "OK", 200

@app.route('/chat', methods=['POST'])
def chat():
    # 1. SECURITY CHECK
    auth_header = request.headers.get('x-vincent-password')
    if auth_header != API_PASSWORD:
        return jsonify({"error": "⛔ ACCESS DENIED: Incorrect API Password."}), 401

    try:
        data = request.json
        message = data.get('message', '')
        model = data.get('model', 'DeepSeek V3.2')
        
        # Payload exactly as required by the provider
        payload = {
            "chat_style": "chat",
            "chatHistory": json.dumps([{"role": "user", "content": message}]),
            "model": model,
            "hacker_is_stinky": "very_stinky",
            "enabled_tools": json.dumps(["image_generator", "image_editor"])
        }

        headers = {
            "api-key": DEEPAI_KEY,
            "Origin": "https://deepai.org",
            "Referer": "https://deepai.org/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        }

        r = requests.post(DEEPAI_URL, data=payload, headers=headers)
        return Response(r.text, mimetype='text/plain')

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
