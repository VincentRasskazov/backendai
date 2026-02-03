from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import os

app = Flask(__name__)
# This allows your VincentAI website to talk to this server
CORS(app)

@app.route('/', methods=['GET'])
def home():
    return "VincentAI Backend is Online!"

@app.route('/chat', methods=['POST'])
def chat():
    # 1. Receive data from your HTML
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
        
    model = data.get('model', 'DeepSeek V3.2')
    message = data.get('message', '')

    # 2. The "Old Script" Logic (DeepAI Spoofing)
    url = "https://api.deepai.org/hacking_is_a_serious_crime"
    headers = {
        "api-key": "tryit-48957598737-7bf6498cad4adf00c76eb3dfa97dc26d",
        "Origin": "https://deepai.org",
        "Referer": "https://deepai.org/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    }
    payload = {
        "chat_style": "chat",
        "chatHistory": json.dumps([{"role": "user", "content": message}]),
        "model": model,
        "hacker_is_stinky": "very_stinky",
        "enabled_tools": json.dumps(["image_generator", "image_editor"])
    }

    try:
        # 3. Send to DeepAI and return the answer to your HTML
        r = requests.post(url, data=payload, headers=headers)
        return r.text
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
