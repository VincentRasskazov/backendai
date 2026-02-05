import os
import json
import uuid
import datetime
import requests
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- PROVIDER LOGIC ---

def stream_venice(message):
    url = "https://outerface.venice.ai/api/inference/chat"
    payload = {
        "conversationId": str(uuid.uuid4())[:7],
        "conversationType": "text",
        "modelId": "zai-org-glm-4.6",
        "prompt": [{"role": "user", "content": message}],
        "requestId": str(uuid.uuid4())[:8],
        "temperature": 0.7,
        "topP": 0.9,
        "webEnabled": True
    }
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://venice.ai",
        "Referer": "https://venice.ai/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "x-venice-version": "interface@python-client"
    }
    
    with requests.post(url, json=payload, headers=headers, stream=True) as r:
        for line in r.iter_lines(decode_unicode=True):
            if line:
                try:
                    obj = json.loads(line)
                    if obj.get("kind") == "content":
                        yield obj.get("content", "")
                except: pass

def stream_overchat(message):
    url = "https://api.overchat.ai/v1/chat/completions"
    payload = {
        "chatId": str(uuid.uuid4()),
        "model": "gpt-5.2-nano",
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": message}],
        "stream": True,
        "personaId": "free-chat-gpt-landing"
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "python-overchat-client/1.0",
        "x-device-uuid": str(uuid.uuid4())
    }
    
    with requests.post(url, json=payload, headers=headers, stream=True) as r:
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                data = line[5:].strip()
                if data == "[DONE]": break
                try:
                    obj = json.loads(data)
                    chunk = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                    if chunk: yield chunk
                except: pass

def stream_talkai(message):
    url = "https://talkai.info/chat/send/"
    payload = {
        "type": "chat",
        "messagesHistory": [{"id": str(uuid.uuid4()), "from": "you", "content": message}],
        "settings": {"model": "gpt-4.1-nano", "temperature": 0.7}
    }
    headers = {"Content-Type": "application/json", "Origin": "https://talkai.info", "Referer": "https://talkai.info/chat/", "User-Agent": "Mozilla/5.0"}

    with requests.post(url, json=payload, headers=headers, stream=True) as r:
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                data = line[5:].strip()
                if not data or data.startswith("GPT") or data == "-1": continue
                if "internal server error" in data.lower(): break
                yield data + " "

def stream_notegpt(message):
    url = "https://notegpt.io/api/v2/chat/stream"
    payload = {
        "conversation_id": str(uuid.uuid4()),
        "message": message,
        "language": "en",
        "model": "gpt-4.1-mini"
    }
    headers = {"Content-Type": "application/json", "Origin": "https://notegpt.io", "Referer": "https://notegpt.io/ai-chat", "User-Agent": "Mozilla/5.0"}

    with requests.post(url, json=payload, headers=headers, stream=True) as r:
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                try:
                    obj = json.loads(line[5:].strip())
                    if obj.get("done"): break
                    if "text" in obj: yield obj["text"]
                except: pass

def stream_useai(message):
    url = "https://use.ai/v1/chat"
    chat_id = str(uuid.uuid4())
    payload = {
        "chatId": chat_id,
        "selectedChatModel": "gateway-gpt-5",
        "message": {
            "id": uuid.uuid4().hex[:16],
            "role": "user",
            "parts": [{"type": "text", "text": message}]
        }
    }
    headers = {"Content-Type": "application/json", "Origin": "https://use.ai", "Referer": f"https://use.ai/chat/{chat_id}", "User-Agent": "Mozilla/5.0"}

    with requests.post(url, json=payload, headers=headers, stream=True) as r:
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                data = line[5:].strip()
                if data == "[DONE]": break
                try:
                    obj = json.loads(data)
                    if obj.get("type") == "text-delta": yield obj.get("delta", "")
                except: pass

def stream_chatplus(message):
    url = "https://chatplus.com/api/chat"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "id": "guest",
        "messages": [{"id": str(uuid.uuid4()), "createdAt": now, "role": "user", "content": message, "parts": [{"type": "text", "text": message}]}],
        "selectedChatModelId": "gpt-4o-mini",
        "token": None
    }
    headers = {"Content-Type": "application/json", "Origin": "https://chatplus.com", "User-Agent": "Mozilla/5.0"}

    with requests.post(url, json=payload, headers=headers, stream=True) as r:
        for chunk in r.iter_lines(decode_unicode=True):
            if chunk and chunk.startswith("0:"):
                text = chunk.split(":", 1)[1].strip()
                if text.startswith('"') and text.endswith('"'): text = text[1:-1]
                yield text

# --- ROUTER ---

@app.route('/health', methods=['GET'])
def health():
    return "OK", 200

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    message = data.get('message', '')
    model = data.get('model', 'venice')

    # Select Provider based on Model ID
    provider_map = {
        "venice": stream_venice,
        "overchat": stream_overchat,
        "talkai": stream_talkai,
        "notegpt": stream_notegpt,
        "useai": stream_useai,
        "chatplus": stream_chatplus
    }

    generator_func = provider_map.get(model, stream_venice)
    
    return Response(stream_with_context(generator_func(message)), mimetype='text/plain')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
