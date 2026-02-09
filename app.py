import os
import json
import uuid
import datetime
import time
import threading
import asyncio
import requests
import g4f  # Requirement: pip install g4f
import websockets # Requirement: pip install websockets
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
# Set this env var in Render to your actual URL (e.g., https://vincent-backend.onrender.com)
# If not set, it falls back to localhost (which you noted doesn't work well on Render free tier).
BACKEND_PUBLIC_URL = os.environ.get('https://backendai-ablv.onrender.com', 'http://127.0.0.1:10000')

# --- RATE LIMITING (In-Memory) ---
# Dictionary to store {ip_address: last_request_timestamp}
request_log = {}
RATE_LIMIT_SECONDS = 3

def is_rate_limited(ip):
    now = time.time()
    last_time = request_log.get(ip, 0)
    if now - last_time < RATE_LIMIT_SECONDS:
        return True
    request_log[ip] = now
    
    # Cleanup old entries periodically (optional optimization)
    if len(request_log) > 1000:
        cleanup_request_log()
    return False

def cleanup_request_log():
    now = time.time()
    # Remove IPs that haven't requested in the last hour
    to_remove = [ip for ip, t in request_log.items() if now - t > 3600]
    for ip in to_remove:
        del request_log[ip]

# --- SELF HEALING / KEEP ALIVE SYSTEM ---
def keep_alive_worker():
    print(f"❤️  Heartbeat system active. Target: {BACKEND_PUBLIC_URL}/health")
    
    while True:
        time.sleep(600) # Wait 10 minutes
        try:
            # Ping the public URL to keep the instance awake
            requests.get(f"{BACKEND_PUBLIC_URL}/health", timeout=10)
            print(f"❤️  Heartbeat sent to {BACKEND_PUBLIC_URL}")
        except Exception as e:
            print(f"⚠️  Heartbeat failed: {e}")

threading.Thread(target=keep_alive_worker, daemon=True).start()

# --- PROVIDERS ---

# 1. VENICE AI (GLM 4.6)
def stream_venice(message):
    url = "https://outerface.venice.ai/api/inference/chat"
    headers = {
        "Content-Type": "application/json",
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
    headers = { "Content-Type": "application/json", "User-Agent": "python-overchat-client/1.0", "x-device-uuid": str(uuid.uuid4()) }
    payload = {
        "chatId": str(uuid.uuid4()), "model": "gpt-5.2-nano",
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": message}],
        "stream": True, "personaId": "free-chat-gpt-landing"
    }
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    if line[5:].strip() == "[DONE]": break
                    try:
                        obj = json.loads(line[5:].strip())
                        chunk = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                        if chunk: yield chunk
                    except: pass
    except Exception as e: yield f"Error: {e}"

# 3. TALK AI (GPT-4.1)
def stream_talkai(message):
    url = "https://talkai.info/chat/send/"
    headers = { "Content-Type": "application/json", "User-Agent": "Mozilla/5.0" }
    payload = {
        "type": "chat", "messagesHistory": [{"id": str(uuid.uuid4()), "from": "you", "content": message}],
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
    headers = { "Content-Type": "application/json", "User-Agent": "Mozilla/5.0" }
    payload = { "conversation_id": str(uuid.uuid4()), "message": message, "language": "en", "model": "gpt-4.1-mini" }
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    try:
                        obj = json.loads(line[5:].strip())
                        if "text" in obj: yield obj["text"]
                    except: pass
    except Exception as e: yield f"Error: {e}"

# 5. USE AI (GPT-5)
def stream_useai(message):
    url = "https://use.ai/v1/chat"
    chat_id = str(uuid.uuid4())
    headers = { "Content-Type": "application/json", "User-Agent": "Mozilla/5.0" }
    payload = {
        "chatId": chat_id, "selectedChatModel": "gateway-gpt-5", "selectedVisibilityType": "private",
        "message": { "id": uuid.uuid4().hex[:16], "role": "user", "parts": [{"type": "text", "text": message}] }
    }
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]": break
                    try:
                        obj = json.loads(data)
                        if obj.get("type") == "text-delta": yield obj.get("delta", "")
                    except: pass
    except Exception as e: yield f"Error: {e}"

# 6. CHATPLUS (GPT-4o)
def stream_chatplus(message):
    url = "https://chatplus.com/api/chat"
    headers = { "Content-Type": "application/json", "User-Agent": "Mozilla/5.0" }
    payload = {
        "id": "guest",
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": message, "parts": [{"type": "text", "text": message}]}],
        "selectedChatModelId": "gpt-4o-mini", "token": None
    }
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for chunk in r.iter_lines(decode_unicode=True):
                if chunk and chunk.startswith("0:"):
                    text = chunk.split(":", 1)[1].strip()
                    if text.startswith('"') and text.endswith('"'): text = text[1:-1]
                    yield text
    except Exception as e: yield f"Error: {e}"

# 7. DEEPAI
def stream_deepai(message, model_name="DeepSeek V3.2"):
    url = "https://api.deepai.org/hacking_is_a_serious_crime" # Placeholder URL as per previous file
    headers = { "api-key": "tryit-48957598737-7bf6498cad4adf00c76eb3dfa97dc26d", "User-Agent": "python-deepai-client/1.0" }
    payload = { "chat_style": "chat", "chatHistory": json.dumps([{"role": "user", "content": message}]), "model": model_name }
    try:
        r = requests.post(url, data=payload, headers=headers, stream=True, timeout=30)
        yield r.text 
    except Exception as e: yield f"Error: {e}"

# --- NEW PROVIDERS ---

# 8. AI HORDE (Stable Horde)
def stream_horde(message):
    # Warning: Depends on volunteer GPUs
    API_KEY = "0000000000"
    HEADERS = { "apikey": API_KEY, "Content-Type": "application/json", "Client-Agent": "VincentAI:1.0:Anonymous" }
    submit_url = "https://stablehorde.net/api/v2/generate/text/async"
    
    formatted_prompt = f"### Instruction:\n{message}\n### Response:\n"
    
    payload = {
        "prompt": formatted_prompt,
        "params": {
            "n": 1, "max_context_length": 1024, "max_length": 512,
            "rep_pen": 1.1, "temperature": 0.7,
            "stop_sequence": ["### Instruction:", "User:", "### Input:"]
        },
        "models": [] # Any available
    }

    try:
        yield "Requesting GPU worker from Horde..."
        submit_req = requests.post(submit_url, headers=HEADERS, json=payload, timeout=10)
        
        if submit_req.status_code != 202:
            yield f"\nError: Horde rejected request ({submit_req.status_code})"
            return

        job_id = submit_req.json()['id']
        status_url = f"https://stablehorde.net/api/v2/generate/text/status/{job_id}"
        
        start_time = time.time()
        while True:
            # Timeout safety (60s)
            if time.time() - start_time > 60:
                yield "\nTimeout: No GPU picked up the job."
                break

            check = requests.get(status_url, headers=HEADERS).json()
            
            if check['done']:
                text = check['generations'][0]['text']
                # Clean up artifacts
                clean = text.replace("### Instruction:", "").replace("### Input:", "").strip()
                yield "\n" + clean # Clear the 'waiting' text
                break
                
            if not check['is_possible']:
                yield "\nError: No workers available for this request."
                break
                
            # Keep alive heartbeat for the frontend stream
            yield " ." 
            time.sleep(2)
            
    except Exception as e:
        yield f"\nHorde Error: {e}"

# 9. MICROSOFT COPILOT (Reverse Engineered WS)
# Wrapper to run async websocket code in sync Flask generator
def stream_copilot(message):
    CHARS = "eEQqRXUu123456CcbBZzhj"
    def generate_conversation_id():
        return ''.join(random.choice(CHARS) for _ in range(21))

    async def run_ws():
        # Inner async function to handle the websocket
        WS_URL = "wss://copilot.microsoft.com/c/api/chat?api-version=2&features=-%2Cncedge%2Cedgepagecontext&setflight=-%2Cncedge%2Cedgepagecontext&ncedge=1"
        payload = {
            "event": "send",
            "conversationId": generate_conversation_id(),
            "content": [{"type": "text", "text": message}],
            "mode": "chat",
            "context": {"edge": "NoConsent"}
        }
        
        results = []
        try:
            async with websockets.connect(WS_URL, ping_interval=None) as ws:
                await ws.send(json.dumps(payload))
                while True:
                    try:
                        data = await asyncio.wait_for(ws.recv(), timeout=15)
                        response = json.loads(data)
                        if "text" in response:
                            results.append(response["text"])
                        if response.get("event") == "done":
                            break
                    except asyncio.TimeoutError:
                        break
        except Exception as e:
            results.append(f"Error: {e}")
        return results

    # Run the async loop and yield results
    # Note: This is a simplified non-streaming implementation for stability in Flask WSGI.
    # It waits for full response then yields. Ideally, use Quart or FastAPI for real async streaming.
    try:
        import random # ensure import is available
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        chunks = loop.run_until_complete(run_ws())
        for chunk in chunks:
            yield chunk
    except Exception as e:
        yield f"System Error: {e}"

# 10. G4F (Free ChatGPT)
def stream_g4f(message):
    try:
        # Using g4f.client for newer versions or direct ChatCompletion
        response = g4f.ChatCompletion.create(
            model=g4f.models.gpt_4,
            messages=[{"role": "user", "content": message}],
            stream=True # Enable streaming if supported by provider
        )
        for chunk in response:
            yield str(chunk)
    except Exception as e:
        yield f"G4F Error (Try again later): {e}"

# --- ROUTER ---
@app.route('/health', methods=['GET'])
def health():
    return "OK", 200

@app.route('/chat', methods=['POST'])
def chat():
    # 1. Rate Limit Check
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if is_rate_limited(client_ip):
        return jsonify({"error": "Rate limit exceeded. Please wait 3 seconds."}), 429

    data = request.json
    message = data.get('message', '')
    model_key = data.get('model', 'venice')

    # Mapping
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
        
    # New Providers
    elif model_key == "horde":
        return Response(stream_with_context(stream_horde(message)), mimetype='text/plain')
    elif model_key == "copilot":
        return Response(stream_with_context(stream_copilot(message)), mimetype='text/plain')
    elif model_key == "g4f":
        return Response(stream_with_context(stream_g4f(message)), mimetype='text/plain')
        
    # DeepAI
    elif model_key.startswith("deepai-"):
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
        return Response(stream_with_context(stream_venice(message)), mimetype='text/plain')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
