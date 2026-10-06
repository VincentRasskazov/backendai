import os
import json
import uuid
import datetime
import time
import threading
import queue
import asyncio
import requests
import random
import re
import g4f
import websockets
import resend
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, messaging, firestore

app = Flask(__name__)
CORS(app)

# --- FIREBASE ADMIN SETUP ---
FIREBASE_PROJECT_ID = "aiproject-67"
firebase_cred_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT')

if firebase_cred_json:
    try:
        cred_dict = json.loads(firebase_cred_json)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': FIREBASE_PROJECT_ID})
        print("✅ Firebase Admin SDK Initialized via Render Environment Variable!")
    except Exception as e:
        print(f"⚠️ Failed to initialize Firebase Admin from env var: {e}")
elif os.path.exists("serviceAccountKey.json"):
    try:
        cred = credentials.Certificate("serviceAccountKey.json")
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': FIREBASE_PROJECT_ID})
        print("✅ Firebase Admin SDK Initialized via local serviceAccountKey.json!")
    except Exception as e:
        print(f"⚠️ Failed to initialize Firebase Admin from file: {e}")
else:
    print("⚠️ FIREBASE_SERVICE_ACCOUNT missing. Firestore & push notifications disabled.")

# --- CONFIGURATION ---
BACKEND_PUBLIC_URL = "https://backendai-ablv.onrender.com"
resend.api_key = os.environ.get("RESEND_API_KEY")

# --- RATE LIMITING (In-Memory) ---
request_log = {}
RATE_LIMIT_SECONDS = 3

def is_rate_limited(ip):
    now = time.time()
    last_time = request_log.get(ip, 0)
    if now - last_time < RATE_LIMIT_SECONDS:
        return True
    request_log[ip] = now
    if len(request_log) > 1000:
        cleanup_request_log()
    return False

def cleanup_request_log():
    now = time.time()
    to_remove = [ip for ip, t in request_log.items() if now - t > 3600]
    for ip in to_remove:
        del request_log[ip]

# --- SELF HEALING / KEEP ALIVE SYSTEM ---
def keep_alive_worker():
    url = f"{BACKEND_PUBLIC_URL}/health"
    print(f"❤️ Heartbeat system active. Target: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; VincentHealth/1.0; +https://vincentai.com)",
        "Accept": "*/*"
    }
    while True:
        time.sleep(600)
        try:
            requests.get(url, headers=headers, timeout=10)
            print(f"❤️ Heartbeat sent to {url}")
        except Exception as e:
            print(f"⚠️ Heartbeat failed: {e}")

threading.Thread(target=keep_alive_worker, daemon=True).start()

# --- PROVIDERS ---
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
                    except:
                        pass
    except Exception as e:
        yield f"Error: {e}"

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
                    if line[5:].strip() == "[DONE]":
                        break
                    try:
                        obj = json.loads(line[5:].strip())
                        chunk = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                        if chunk:
                            yield chunk
                    except:
                        pass
    except Exception as e:
        yield f"Error: {e}"

def stream_talkai(message):
    url = "https://talkai.info/chat/send/"
    headers = {
        "Content-Type": "application/json",
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
                    if not data or data.startswith("GPT") or data == "-1":
                        continue
                    yield data + " "
    except Exception as e:
        yield f"Error: {e}"

def stream_notegpt(message):
    url = "https://notegpt.io/api/v2/chat/stream"
    headers = {
        "Content-Type": "application/json",
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
                        if "text" in obj:
                            yield obj["text"]
                    except:
                        pass
    except Exception as e:
        yield f"Error: {e}"

def stream_useai(message):
    url = "https://use.ai/v1/chat"
    chat_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
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
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        if obj.get("type") == "text-delta":
                            yield obj.get("delta", "")
                    except:
                        pass
    except Exception as e:
        yield f"Error: {e}"

def stream_chatplus(message):
    url = "https://chatplus.com/api/chat"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    payload = {
        "id": "guest",
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": message, "parts": [{"type": "text", "text": message}]}],
        "selectedChatModelId": "gpt-4o-mini",
        "token": None
    }
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for chunk in r.iter_lines(decode_unicode=True):
                if chunk and chunk.startswith("0:"):
                    text = chunk.split(":", 1)[1].strip()
                    if text.startswith('"') and text.endswith('"'):
                        text = text[1:-1]
                    yield text
    except Exception as e:
        yield f"Error: {e}"

def stream_deepai(message, model_name="DeepSeek V3.2"):
    url = "https://api.deepai.org/hacking_is_a_serious_crime"
    headers = {
        "api-key": "tryit-48957598737-7bf6498cad4adf00c76eb3dfa97dc26d",
        "User-Agent": "python-deepai-client/1.0"
    }
    payload = {
        "chat_style": "chat",
        "chatHistory": json.dumps([{"role": "user", "content": message}]),
        "model": model_name
    }
    try:
        r = requests.post(url, data=payload, headers=headers, stream=True, timeout=30)
        yield r.text 
    except Exception as e:
        yield f"Error: {e}"

def stream_horde(message):
    API_KEY = "0000000000"
    HEADERS = {
        "apikey": API_KEY,
        "Content-Type": "application/json",
        "Client-Agent": "VincentAI:1.0:Anonymous"
    }
    submit_url = "https://stablehorde.net/api/v2/generate/text/async"
    formatted_prompt = f"### Instruction:\n{message}\n### Response:\n"
    payload = {
        "prompt": formatted_prompt,
        "params": {
            "n": 1,
            "max_context_length": 1024,
            "max_length": 512,
            "rep_pen": 1.1,
            "temperature": 0.7,
            "stop_sequence": ["### Instruction:", "User:", "### Input:"]
        },
        "models": []
    }
    try:
        yield "Requesting GPU worker from Horde..."
        submit_req = requests.post(submit_url, headers=HEADERS, json=payload, timeout=10)
        if submit_req.status_code != 202:
            yield f"\nError: Horde rejected ({submit_req.status_code})"
            return
        job_id = submit_req.json()['id']
        status_url = f"https://stablehorde.net/api/v2/generate/text/status/{job_id}"
        start_time = time.time()
        while True:
            if time.time() - start_time > 60:
                yield "\nTimeout: No GPU picked up the job."
                break
            check = requests.get(status_url, headers=HEADERS).json()
            if check['done']:
                text = check['generations'][0]['text']
                clean = text.replace("### Instruction:", "").replace("### Input:", "").strip()
                yield "\n" + clean
                break
            if not check['is_possible']:
                yield "\nError: No workers available."
                break
            yield " ." 
            time.sleep(2)
    except Exception as e:
        yield f"\nHorde Error: {e}"

def stream_copilot(message):
    q = queue.Queue()
    CHARS = "eEQqRXUu123456CcbBZzhj"

    def generate_conversation_id():
        return ''.join(random.choice(CHARS) for _ in range(21))

    async def run_ws():
        ws_url = "wss://copilot.microsoft.com/c/api/chat?api-version=2&features=-%2Cncedge%2Cedgepagecontext&setflight=-%2Cncedge%2Cedgepagecontext&ncedge=1"
        payload = {
            "event": "send",
            "conversationId": generate_conversation_id(),
            "content": [{"type": "text", "text": message}],
            "mode": "chat",
            "context": {"edge": "NoConsent"}
        }
        try:
            async with websockets.connect(ws_url, ping_interval=None) as ws:
                await ws.send(json.dumps(payload))
                while True:
                    try:
                        data = await asyncio.wait_for(ws.recv(), timeout=15)
                        response = json.loads(data)
                        if "text" in response:
                            q.put(response["text"])
                        if response.get("event") == "done":
                            break
                    except asyncio.TimeoutError:
                        break
        except Exception as e:
            q.put(f"[Error: {e}]")
        finally:
            q.put(None)

    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_ws())
        loop.close()

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    while True:
        chunk = q.get()
        if chunk is None:
            break
        yield chunk

def stream_g4f(message):
    try:
        response = g4f.ChatCompletion.create(
            model=g4f.models.gpt_4,
            messages=[{"role": "user", "content": message}],
            stream=True
        )
        for chunk in response:
            yield str(chunk)
    except Exception as e:
        yield f"G4F Error: {e}"

def stream_openai(messages, api_key, model="gpt-4o-mini"):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "stream": True
    }
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk_json = json.loads(data_str)
                        content = chunk_json.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content:
                            yield content
                    except:
                        pass
    except Exception as e:
        yield f"[OpenAI Error: {e}]"

# --- LEAD CATCHER ENGINE ---
def send_lead_email(owner_email, lead_text, client_id):
    """Fires an email via Resend in the background."""
    if not owner_email or not resend.api_key:
        print("⚠️ Email delivery skipped: Missing owner_email or RESEND_API_KEY")
        return
        
    try:
        resend.Emails.send({
            "from": "Vincent AI <leads@vincentrasskazov.com.au>",
            "to": owner_email,
            "subject": f"🚨 New Lead Captured: {client_id}",
            "html": f"""
            <div style="font-family: sans-serif; padding: 20px;">
                <h2 style="color: #2563eb;">New Lead Alert</h2>
                <p>Your AI assistant just captured a new lead:</p>
                <div style="background: #f3f4f6; padding: 15px; border-radius: 8px; font-size: 16px; font-weight: bold;">
                    {lead_text}
                </div>
                <p style="color: #6b7280; font-size: 12px; margin-top: 20px;">Powered by Vincent Rasskazov AI</p>
            </div>
            """
        })
        print(f"✅ Lead email sent successfully to {owner_email}")
    except Exception as e:
        print(f"⚠️ Failed to send lead email: {e}")

def intercept_leads(generator, owner_email, client_id):
    """Wraps the text stream, hiding the ||LEAD|| tag from the user and triggering the email."""
    accumulated = ""
    for chunk in generator:
        accumulated += chunk
        
        # Check if the AI is attempting to write a lead tag
        if "||" in accumulated:
            # If the full tag has been generated, extract it
            if "||LEAD:" in accumulated and accumulated.count("||") >= 2:
                start_idx = accumulated.find("||LEAD:")
                end_idx = accumulated.find("||", start_idx + 2) + 2
                
                tag_string = accumulated[start_idx:end_idx]
                lead_data = tag_string.replace("||LEAD:", "").replace("||", "").strip()
                
                # Fire the email off in a background thread so the chat doesn't freeze
                threading.Thread(target=send_lead_email, args=(owner_email, lead_data, client_id), daemon=True).start()
                
                # Erase the tag from the buffer so the customer never sees it
                accumulated = accumulated.replace(tag_string, "")
                yield accumulated
                accumulated = ""
        else:
            # Safe to yield normally
            yield accumulated
            accumulated = ""
            
    # Yield any remaining text
    if accumulated:
        # Final safety check to strip partial tags
        clean_final = re.sub(r'\|\|LEAD:.*?\|\|', '', accumulated)
        yield clean_final

# --- B2B CLIENT DATABASE HELPER ---
def get_b2b_client(client_id):
    if not firebase_admin._apps:
        return None
    try:
        db = firestore.client()
        doc = db.collection('b2b_clients').document(client_id).get()
        return doc.to_dict() if doc.exists else None
    except Exception as e:
        print(f"⚠️ Error reading b2b_clients from Firestore: {e}")
        return None

# --- ROUTER ---
@app.route('/health', methods=['GET'])
def health():
    return "OK", 200

@app.route('/notify', methods=['POST'])
def notify():
    if not firebase_admin._apps:
        return jsonify({"error": "Firebase Admin not configured on server"}), 500

    data = request.json or {}
    token = data.get('fcmToken')
    title = data.get('title', 'New Message')
    body = data.get('body', 'You have a new message.')

    if not token:
        return jsonify({"error": "Missing fcmToken"}), 400

    try:
        msg = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            token=token,
        )
        response = messaging.send(msg)
        return jsonify({"success": True, "message_id": response}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/chat', methods=['POST'])
def chat():
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if is_rate_limited(client_ip):
        return jsonify({"error": "Rate limit exceeded. Wait 3s."}), 429

    data = request.json or {}

    student_data = data.get('studentData')
    if student_data and firebase_admin._apps:
        try:
            db = firestore.client()
            student_data['timestamp'] = firestore.SERVER_TIMESTAMP
            db.collection('insyd_survey_submissions').add(student_data)
            print("✅ Survey data saved to Firestore!")
        except Exception as e:
            print(f"⚠️ Failed to save to Firestore: {e}")

    message = data.get('message', '')
    model_key = data.get('model', 'venice')

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
    elif model_key == "horde":
        return Response(stream_with_context(stream_horde(message)), mimetype='text/plain')
    elif model_key == "copilot":
        return Response(stream_with_context(stream_copilot(message)), mimetype='text/plain')
    elif model_key == "g4f":
        return Response(stream_with_context(stream_g4f(message)), mimetype='text/plain')
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
        return Response(stream_with_context(stream_deepai(message, deepai_map.get(model_key, "DeepSeek V3.2"))), mimetype='text/plain')
    else:
        return Response(stream_with_context(stream_venice(message)), mimetype='text/plain')

# --- DEDICATED MULTI-TENANT B2B ROUTE ---
@app.route('/b2b/chat', methods=['POST'])
def b2b_chat():
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if is_rate_limited(client_ip):
        return jsonify({"error": "Rate limit exceeded. Wait 3s."}), 429

    data = request.json or {}
    client_id = data.get('client_id')
    user_message = data.get('message', '').strip()
    history = data.get('history', [])

    if not client_id or not user_message:
        return jsonify({"error": "Missing client_id or message"}), 400

    client_data = get_b2b_client(client_id)
    if not client_data or not client_data.get('is_active', False):
        return jsonify({"error": "Bot unavailable or inactive"}), 403

    system_prompt = client_data.get('system_prompt', 'You are a helpful assistant.')
    commercial_key = client_data.get('openai_api_key')
    owner_email = client_data.get('owner_email') 

    # Choose the engine
    if commercial_key:
        messages = [{"role": "system", "content": system_prompt}]
        for turn in history:
            messages.append({"role": turn.get("role", "user"), "content": turn.get("content", "")})
        messages.append({"role": "user", "content": user_message})
        base_stream = stream_openai(messages, commercial_key)
    else:
        history_transcript = ""
        for turn in history:
            role = "Customer" if turn.get("role") == "user" else "Assistant"
            history_transcript += f"{role}: {turn.get('content', '')}\n"
        
        full_prompt = (
            f"### System Instructions:\n{system_prompt}\n\n"
            f"### Recent Conversation History:\n{history_transcript}\n"
            f"Customer: {user_message}\n"
            f"Assistant:"
        )
        if len(full_prompt) > 9000:
            excess = len(full_prompt) - 9000
            history_transcript = history_transcript[excess:]
            full_prompt = f"### System Instructions:\n{system_prompt}\n\n### Recent Conversation History:\n{history_transcript}\nCustomer: {user_message}\nAssistant:"
            
        base_stream = stream_copilot(full_prompt)

    # Wrap the chosen engine in the lead interceptor
    secure_stream = intercept_leads(base_stream, owner_email, client_id)
    
    return Response(stream_with_context(secure_stream), mimetype='text/plain')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
