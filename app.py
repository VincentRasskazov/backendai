import os
import json
import uuid
import datetime
import time
import threading
import requests
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- SELF HEALING / KEEP ALIVE SYSTEM ---
# Render spins down free tier apps after 15 mins of inactivity.
# This thread pings the server every 10 mins to keep it alive.
def keep_alive_worker():
    port = int(os.environ.get('PORT', 10000))
    url = f"http://127.0.0.1:{port}/health"
    print(f"❤️  Heartbeat system active. Pinging {url} every 10 minutes.")
    
    while True:
        time.sleep(600) # Wait 10 minutes
        try:
            requests.get(url)
            print("❤️  Heartbeat sent (Keep-Alive)")
        except Exception as e:
            print(f"⚠️  Heartbeat failed: {e}")

# Start the background thread
threading.Thread(target=keep_alive_worker, daemon=True).start()

# --- PROVIDER LOGIC ---

# 1. VENICE AI (GLM 4.6)
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

# 2. OVERCHAT (GPT-5 Nano)
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

# 3. TALK AI (GPT-4.1)
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

# 4. NOTEGPT (GPT-4 Mini)
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

# 5. USE AI (GPT-5)
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

# 6. CHATPLUS (GPT-4o)
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

# 7. DEEPAI (Specific Model Logic)
def stream_deepai(message, model_name="DeepSeek V3.2"):
    url = "https://api.deepai.org/hacking_is_a_serious_crime"
    headers = {
        "api-key": "tryit-48957598737-7bf6498cad4adf00c76eb3dfa97dc26d",
        "User-Agent": "python-deepai-client/1.0"
    }
    payload = {
        "chat_style": "chat", 
        "chatHistory": json.dumps([{"role": "user", "content": message}]), 
        "model": model_name,
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
    model_key = data.get('model', 'venice')

    # Mapping frontend keys to functions or specific configurations
    if model_key == "venice":
        return Response(stream_with_context(stream_venice(message)), mimetype='text/plain')
    
    elif model_key == "overchat":
        return Response(stream_with_context(stream_overchat(message)), mimetype='text/plain')
    
    elif model_key == "talkai":
        return Response(stream_with_context(stream_talkai(message)), mimetype='text/plain')
    
    elif model_key == "notegpt":
        return Response(stream_with_context(stream_notegpt(message)), mimetype='text/plain')
    
    elif model_key == "useai":
        return Response(stream_with_context(stream_useai(message)), mimetype='text/plain')
    
    elif model_key == "chatplus":
        return Response(stream_with_context(stream_chatplus(message)), mimetype='text/plain')
    
    # --- DeepAI Variants ---
    elif model_key.startswith("deepai-"):
        # Map frontend key to DeepAI's internal model name
        deepai_map = {
            "deepai-deepseek": "DeepSeek V3.2",
            "deepai-llama": "Llama 3.3 70B Instruct",
            "deepai-qwen": "Qwen3 30B",
            "deepai-4omini": "GPT-4o mini",
            "deepai-gemma3": "Gemma 3 12B",
            "deepai-gemma2": "Gemma2 9B",
            "deepai-4nano": "GPT-4.1 Nano"
        }
        specific_model = deepai_map.get(model_key, "DeepSeek V3.2")
        return Response(stream_with_context(stream_deepai(message, specific_model)), mimetype='text/plain')

    else:
        # Default
        return Response(stream_with_context(stream_venice(message)), mimetype='text/plain')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
