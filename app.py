import os
import json
import uuid
import datetime
import requests
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- 1. VENICE AI (GLM 4.6) ---
def stream_venice(message):
    url = "https://outerface.venice.ai/api/inference/chat"
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://venice.ai",
        "Referer": "https://venice.ai/",
        "User-Agent": "python-venice-client/1.0",
        "x-venice-version": "interface@python-client"
    }
    payload = {
        "conversationId": str(uuid.uuid4())[:7],
        "modelId": "zai-org-glm-4.6",
        "prompt": [{"role": "user", "content": message}],
        "requestId": str(uuid.uuid4())[:8],
        "temperature": 0.7,
        "webEnabled": True
    }
    
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line:
                    try:
                        obj = json.loads(line)
                        if obj.get("kind") == "content":
                            yield obj.get("content", "")
                    except: pass
    except Exception as e: yield f"Error: {e}"

# --- 2. OVERCHAT (GPT-5 Nano) ---
def stream_overchat(message):
    url = "https://api.overchat.ai/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "python-overchat-client/1.0",
        "x-device-uuid": str(uuid.uuid4())
    }
    payload = {
        "chatId": str(uuid.uuid4()),
        "model": "gpt-5.2-nano",
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": message}],
        "stream": True,
        "personaId": "free-chat-gpt-landing"
    }

    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]": break
                    try:
                        obj = json.loads(data)
                        chunk = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                        if chunk: yield chunk
                    except: pass
    except Exception as e: yield f"Error: {e}"

# --- 3. TALK AI (GPT-4.1) ---
def stream_talkai(message):
    url = "https://talkai.info/chat/send/"
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://talkai.info",
        "Referer": "https://talkai.info/chat/",
        "User-Agent": "Mozilla/5.0"
    }
    payload = {
        "type": "chat",
        "messagesHistory": [{"id": str(uuid.uuid4()), "from": "you", "content": message}],
        "settings": {"model": "gpt-4.1-nano", "temperature": 0.7}
    }

    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    data = line[5:].strip()
                    if not data or data.startswith("GPT") or data == "-1": continue
                    yield data + " "
    except Exception as e: yield f"Error: {e}"

# --- 4. NOTEGPT (GPT-4 Mini) ---
def stream_notegpt(message):
    url = "https://notegpt.io/api/v2/chat/stream"
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://notegpt.io",
        "Referer": "https://notegpt.io/ai-chat",
        "User-Agent": "Mozilla/5.0"
    }
    payload = {
        "conversation_id": str(uuid.uuid4()),
        "message": message,
        "language": "en",
        "model": "gpt-4.1-mini"
    }

    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    try:
                        obj = json.loads(line[5:].strip())
                        if obj.get("done"): break
                        if "text" in obj: yield obj["text"]
                    except: pass
    except Exception as e: yield f"Error: {e}"

# --- 5. USE AI (Gateway 5) ---
def stream_useai(message):
    url = "https://use.ai/v1/chat"
    chat_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://use.ai",
        "Referer": f"https://use.ai/chat/{chat_id}",
        "User-Agent": "Mozilla/5.0"
    }
    payload = {
        "chatId": chat_id,
        "selectedChatModel": "gateway-gpt-5",
        "selectedVisibilityType": "private",
        "message": {
            "id": uuid.uuid4().hex[:16],
            "role": "user",
            "parts": [{"type": "text", "text": message}]
        }
    }

    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]": break
                    try:
                        obj = json.loads(data)
                        if obj.get("type") == "text-delta":
                            yield obj.get("delta", "")
                    except: pass
    except Exception as e: yield f"Error: {e}"

# --- 6. CHATPLUS (GPT-4o) ---
def stream_chatplus(message):
    url = "https://chatplus.com/api/chat"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://chatplus.com",
        "User-Agent": "Mozilla/5.0"
    }
    payload = {
        "id": "guest",
        "messages": [{
            "id": str(uuid.uuid4()),
            "createdAt": now,
            "role": "user",
            "content": message,
            "parts": [{"type": "text", "text": message}]
        }],
        "selectedChatModelId": "gpt-4o-mini",
        "token": None
    }

    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for chunk in r.iter_lines(decode_unicode=True):
                if chunk and chunk.startswith("0:"):
                    text = chunk.split(":", 1)[1].strip()
                    if text.startswith('"') and text.endswith('"'): text = text[1:-1]
                    yield text
    except Exception as e: yield f"Error: {e}"

# --- 7. DEEPAI (DeepSeek/Llama) ---
def stream_deepai(message):
    url = "https://api.deepai.org/hacking_is_a_serious_crime"
    headers = {
        "api-key": "tryit-48957598737-7bf6498cad4adf00c76eb3dfa97dc26d",
        "User-Agent": "python-deepai-client/1.0"
    }
    payload = {
        "chat_style": "chat", 
        "chatHistory": json.dumps([{"role": "user", "content": message}]), 
        "model": "DeepSeek V3.2",
        "hacker_is_stinky": "very_stinky"
    }
    try:
        r = requests.post(url, data=payload, headers=headers, stream=True, timeout=30)
        yield r.text 
    except Exception as e: yield f"Error: {e}"


# --- ROUTER ---
@app.route('/health', methods=['GET'])
def health():
    return "OK", 200

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    message = data.get('message', '')
    model = data.get('model', 'venice')

    providers = {
        "venice": stream_venice,
        "overchat": stream_overchat,
        "talkai": stream_talkai,
        "notegpt": stream_notegpt,
        "useai": stream_useai,
        "chatplus": stream_chatplus,
        "deepai": stream_deepai
    }
    
    # Pick the function, default to Venice if not found
    generator = providers.get(model, stream_venice)
    return Response(stream_with_context(generator(message)), mimetype='text/plain')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
