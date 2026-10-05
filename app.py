import os
import json
import uuid
import datetime
import time
import threading
import asyncio
import queue
import requests
import random
import g4f  # Requirement: pip install g4f
import websockets  # Requirement: pip install websockets
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import firebase_admin  # Requirement: pip install firebase-admin
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

# --- RATE LIMITING & KEEP ALIVE ---
request_log = {}
def is_rate_limited(ip):
    now = time.time()
    last = request_log.get(ip, 0)
    if now - last < 3: return True
    request_log[ip] = now
    return False

# --- PROVIDERS ---

# MICROSOFT COPILOT (Updated for real-time true streaming)
def stream_copilot(message):
    CHARS = "eEQqRXUu123456CcbBZzhj"
    def generate_conversation_id():
        return ''.join(random.choice(CHARS) for _ in range(21))

    q = queue.Queue()

    def run_in_thread():
        async def run_ws():
            WS_URL = "wss://copilot.microsoft.com/c/api/chat?api-version=2&features=-%2Cncedge%2Cedgepagecontext&setflight=-%2Cncedge%2Cedgepagecontext&ncedge=1"
            payload = {
                "event": "send",
                "conversationId": generate_conversation_id(),
                "content": [{"type": "text", "text": message}],
                "mode": "chat",
                "context": {"edge": "NoConsent"}
            }
            try:
                async with websockets.connect(WS_URL, ping_interval=None) as ws:
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
                q.put(f"Error: {e}")
            finally:
                q.put(None) # Sentinel to stop generator

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_ws())

    # Start the websocket in a background thread so the generator can yield chunks instantly
    threading.Thread(target=run_in_thread, daemon=True).start()

    # Yield chunks as they arrive in the queue
    while True:
        chunk = q.get()
        if chunk is None:
            break
        yield chunk

# --- B2B CLIENT DATABASE HELPER ---
def get_b2b_client(client_id):
    if not firebase_admin._apps: return None
    try:
        return firestore.client().collection('b2b_clients').document(client_id).get().to_dict()
    except Exception: return None

# --- DEDICATED MULTI-TENANT B2B ROUTE ---
@app.route('/b2b/chat', methods=['POST'])
def b2b_chat():
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if is_rate_limited(client_ip):
        return jsonify({"error": "Rate limit exceeded. Wait 3s."}), 429

    data = request.json or {}
    client_id = data.get('client_id')
    user_message = data.get('message', '').strip()
    history = data.get('history', [])  # Grabbing the chat history array from the frontend

    if not client_id or not user_message:
        return jsonify({"error": "Missing client_id or message"}), 400

    client_data = get_b2b_client(client_id)
    if not client_data or not client_data.get('is_active', False):
        return jsonify({"error": "Bot unavailable or inactive"}), 403

    system_prompt = client_data.get('system_prompt', 'You are a helpful assistant.')
    
    # 1. Compile History
    history_text = ""
    for msg in history:
        role = "Customer" if msg['role'] == 'user' else "Assistant"
        history_text += f"{role}: {msg['content']}\n"

    # 2. Safely Enforce 10k Character Limit (Truncate history, preserve prompt)
    core_length = len(system_prompt) + len(user_message) + 200
    if len(history_text) > (9500 - core_length):
        # Slice off the oldest messages if history is too long
        history_text = "..." + history_text[-(9500 - core_length):]

    full_prompt = (
        f"### System Instructions:\n{system_prompt}\n\n"
        f"### Conversation History:\n{history_text}\n"
        f"### Current Customer Inquiry:\n{user_message}\n\n"
        f"### Response:"
    )

    return Response(stream_with_context(stream_copilot(full_prompt)), mimetype='text/plain')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
