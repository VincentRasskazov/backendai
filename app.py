import os
import json
import time
import requests
import threading
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
# Enable CORS so your GitHub Page can talk to this backend
CORS(app)

# --- CONFIGURATION ---
DEEPAI_URL = "https://api.deepai.org/hacking_is_a_serious_crime"
DEEPAI_KEY = "tryit-48957598737-7bf6498cad4adf00c76eb3dfa97dc26d"

# --- KEEP-ALIVE MECHANISM ---
# Render free tier sleeps after 15 mins. This pings it every 14 mins.
def keep_alive():
    """Background thread to ping the server and keep it awake."""
    # We need to wait a bit for the server to start before we can ping it
    time.sleep(10) 
    
    # Get the URL from environment or default to localhost if testing
    app_url = os.environ.get('RENDER_EXTERNAL_URL')
    
    if not app_url:
        print("No external URL found (running locally?). Keep-alive disabled.")
        return

    print(f"Starting keep-alive for {app_url}")
    while True:
        try:
            time.sleep(840) # 14 minutes
            print("Pinging self to stay awake...")
            requests.get(f"{app_url}/health")
        except Exception as e:
            print(f"Self-ping failed: {e}")

# Start the keep-alive thread
threading.Thread(target=keep_alive, daemon=True).start()


# --- ROUTES ---

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status": "online",
        "message": "VincentAI Backend is Running",
        "usage": "Send POST requests to /chat"
    })

@app.route('/health', methods=['GET'])
def health():
    # Simple endpoint for the keep-alive script to hit
    return "OK", 200

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        message = data.get('message', '')
        model = data.get('model', 'DeepSeek V3.2')

        if not message:
            return jsonify({"error": "Message is required"}), 400

        # Prepare Payload exactly as required by freedeepai.py
        payload = {
            "chat_style": "chat",
            "chatHistory": json.dumps([{"role": "user", "content": message}]),
            "model": model,
            "hacker_is_stinky": "very_stinky", # Required by API
            "enabled_tools": json.dumps(["image_generator", "image_editor"])
        }

        # Prepare Headers exactly as required by freedeepai.py
        headers = {
            "api-key": DEEPAI_KEY,
            "Origin": "https://deepai.org",
            "Referer": "https://deepai.org/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded" # Important for requests.post(data=...)
        }

        # Send request to DeepAI
        # Note: using 'data=' instead of 'json=' because the payload format implies form-encoded
        response = requests.post(DEEPAI_URL, data=payload, headers=headers)

        # Return the raw text from DeepAI (it usually returns just the answer string)
        return Response(response.text, mimetype='text/plain')

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
