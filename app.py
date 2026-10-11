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

# ==========================================
# 1. DATABASE & API SETUP
# ==========================================
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
    print("⚠️ FIREBASE_SERVICE_ACCOUNT missing. Firestore disabled.")

BACKEND_PUBLIC_URL = "https://backendai-ablv.onrender.com"
resend.api_key = os.environ.get("RESEND_API_KEY")

# ==========================================
# 2. SERVER OPTIMIZATIONS
# ==========================================
request_log = {}
RATE_LIMIT_SECONDS = 3

def is_rate_limited(ip):
    now = time.time()
    last_time = request_log.get(ip, 0)
    if now - last_time < RATE_LIMIT_SECONDS:
        return True
    request_log[ip] = now
    
    if len(request_log) > 500:
        cleanup_request_log(now)
    return False

def cleanup_request_log(current_time):
    to_remove = [ip for ip, t in request_log.items() if current_time - t > 3600]
    for ip in to_remove:
        del request_log[ip]

def keep_alive_worker():
    url = f"{BACKEND_PUBLIC_URL}/health"
    headers = {"User-Agent": "VincentHealth/1.0", "Accept": "*/*"}
    while True:
        time.sleep(840) 
        try:
            requests.get(url, headers=headers, timeout=5)
        except Exception:
            pass

threading.Thread(target=keep_alive_worker, daemon=True).start()

# ==========================================
# 3. BASE SYSTEM PROMPT (The "Brain")
# ==========================================
BASE_SYSTEM_PROMPT = """You are a helpful, professional virtual assistant for a business.
Your goal is to answer customer questions accurately using ONLY the 'Business Data' provided below. 
If the answer is not in the Business Data, politely say you don't know and ask for their phone number so the team can call them. DO NOT make up prices, services, or facts.

LEAD CAPTURE RULES:
1. Be conversational. Ask questions to figure out what specific service the customer needs.
2. Once you know what they need, gently ask for their name and phone number to arrange a callback.
3. As soon as they provide their name and phone number, you MUST output a secret tracking tag summarizing the lead.
   Format the tag EXACTLY like this with pipe symbols: ||LEAD: Name | Phone | Brief summary of what they need||
   CRITICAL: ONLY OUTPUT THIS TAG EXACTLY ONCE. If you have already thanked them for their details in a previous message, DO NOT output the tag again.
4. After outputting the tag, warmly thank the customer and tell them the team will call them shortly.

CRITICAL RESTRICTION: You are the Assistant. Only generate the Assistant's reply. NEVER write dialogue for the Customer.
"""

# ==========================================
# 4. AI PROVIDERS
# ==========================================
def stream_copilot(message):
    q = queue.Queue()
    def generate_conversation_id():
        return ''.join(random.choice("eEQqRXUu123456CcbBZzhj") for _ in range(21))

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
        except Exception:
            q.put("\n\n*The connection was interrupted. Please ask your question again.*")
        finally:
            q.put(None)

    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_ws())
        loop.close()

    threading.Thread(target=worker, daemon=True).start()
    while True:
        chunk = q.get()
        if chunk is None:
            break
        yield chunk

def stream_openai(messages, api_key, model="gpt-4o-mini"):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {"model": model, "messages": messages, "stream": True}
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
    except Exception:
        yield "\n\n*Connection error. Please try again.*"

# ==========================================
# 5. LEAD CATCHER ENGINE (Anti-Spam Locked)
# ==========================================
def send_lead_email(owner_email, lead_text, client_id):
    if not owner_email or not resend.api_key:
        return
        
    parts = [p.strip() for p in lead_text.split('|')]
    
    if len(parts) >= 3:
        display_html = f"""
            <p><b>Name:</b> {parts[0]}</p>
            <p><b>Phone:</b> {parts[1]}</p>
            <p><b>Job Details:</b> {parts[2]}</p>
        """
    else:
        display_html = f"<p><b>Lead Details:</b> {lead_text}</p>"

    try:
        resend.Emails.send({
            "from": "Vincent AI <leads@vincentrasskazov.com.au>",
            "to": owner_email,
            "subject": f"New Lead Captured: {client_id}",
            "html": f"""
            <div style="font-family: sans-serif; padding: 20px; max-width: 600px;">
                <h2 style="color: #2563eb; margin-bottom: 5px;">New Lead Alert</h2>
                <p style="color: #4b5563; margin-top: 0;">Your AI assistant just captured a new lead.</p>
                <div style="background: #f8fafc; padding: 20px; border-left: 4px solid #3b82f6; border-radius: 4px; font-size: 16px; margin-top: 20px;">
                    {display_html}
                </div>
                <p style="color: #9ca3af; font-size: 12px; margin-top: 30px;">Powered by Vincent Rasskazov AI</p>
            </div>
            """
        })
        print(f"✅ Lead email sent successfully to {owner_email}")
    except Exception as e:
        print(f"⚠️ Failed to send lead email: {e}")

def intercept_leads(generator, owner_email, client_id):
    buffer = ""
    inside_tag = False
    email_sent = False # HARD LOCK: Max 1 email per user message
    
    for chunk in generator:
        buffer += chunk
        
        if not inside_tag:
            if "||" in buffer:
                if "||LEAD:" in buffer:
                    inside_tag = True
                    start_idx = buffer.find("||LEAD:")
                    if start_idx > 0:
                        yield buffer[:start_idx]
                    buffer = buffer[start_idx:]
                else:
                    idx = buffer.find("||")
                    after_pipes = buffer[idx+2:]
                    
                    if "LEAD:".startswith(after_pipes):
                        if idx > 0:
                            yield buffer[:idx]
                            buffer = buffer[idx:]
                    else:
                        yield buffer[:idx+2]
                        buffer = buffer[idx+2:]
                        
            elif buffer.endswith("|"):
                if len(buffer) > 1:
                    yield buffer[:-1]
                buffer = "|"
            else:
                yield buffer
                buffer = ""
                
        else:
            if "||" in buffer[2:]: 
                end_idx = buffer.find("||", 2) + 2
                tag_string = buffer[:end_idx]
                lead_data = tag_string.replace("||LEAD:", "").replace("||", "").strip()
                
                # ONLY fire if we haven't sent one for this specific chat burst yet
                if not email_sent:
                    threading.Thread(target=send_lead_email, args=(owner_email, lead_data, client_id), daemon=True).start()
                    email_sent = True
                
                buffer = buffer[end_idx:]
                inside_tag = False
            elif len(buffer) > 500:
                yield buffer
                buffer = ""
                inside_tag = False

    if buffer:
        if inside_tag:
            lead_data = buffer.replace("||LEAD:", "").replace("||", "").strip()
            if lead_data and not email_sent:
                threading.Thread(target=send_lead_email, args=(owner_email, lead_data + " [Incomplete]", client_id), daemon=True).start()
                email_sent = True
        else:
            yield buffer



# ==========================================
# 6. MCP SERVER (GEMINI INTEGRATION)
# ==========================================
mcp_sessions = {}

# 1. Provide OAuth Discovery so Gemini knows this is a valid server
@app.route('/.well-known/oauth-authorization-server', methods=['GET', 'OPTIONS'])
@app.route('/.well-known/openid-configuration', methods=['GET', 'OPTIONS'])
def well_known_discovery():
    if request.method == 'OPTIONS':
        resp = Response(status=204)
        resp.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return resp

    metadata = {
        "issuer": BACKEND_PUBLIC_URL,
        "authorization_endpoint": f"{BACKEND_PUBLIC_URL}/oauth/auth",
        "token_endpoint": f"{BACKEND_PUBLIC_URL}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"]
    }
    resp = jsonify(metadata)
    resp.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
    return resp

# 2. Main MCP stream with strict CORS for Gemini
@app.route('/mcp', methods=['GET', 'OPTIONS'])
def mcp_sse():
    if request.method == 'OPTIONS':
        resp = Response(status=204)
        resp.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
        resp.headers['Access-Control-Allow-Credentials'] = 'true'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, x-mcp-session'
        return resp

    session_id = str(uuid.uuid4())
    q = queue.Queue()
    mcp_sessions[session_id] = q

    def generate():
        yield ": start\n\n"
        post_url = f"{BACKEND_PUBLIC_URL}/mcp/message?session_id={session_id}"
        yield f"event: endpoint\ndata: {post_url}\n\n"
        while True:
            try:
                message = q.get(timeout=15)
                yield f"data: {json.dumps(message)}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers['Cache-Control'] = 'no-cache, no-transform'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response

# 3. Message endpoint for JSON-RPC
@app.route('/mcp/message', methods=['POST', 'OPTIONS'])
def mcp_message():
    if request.method == 'OPTIONS':
        resp = Response(status=204)
        resp.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
        resp.headers['Access-Control-Allow-Credentials'] = 'true'
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, x-mcp-session'
        return resp
        
    session_id = request.args.get('session_id')
    req = request.json or {}
    method = req.get("method")
    msg_id = req.get("id")

    response = {"jsonrpc": "2.0", "id": msg_id}

    if method == "initialize":
        response["result"] = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "vincent-firestore-mcp", "version": "1.0"}
        }
    elif method == "notifications/initialized":
        resp = Response(status=202)
        resp.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
        resp.headers['Access-Control-Allow-Credentials'] = 'true'
        return resp
    elif method == "tools/list":
        response["result"] = {
            "tools": [
                {
                    "name": "get_client_data",
                    "description": "Read business_data, owner_email, and is_active for a client.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"client_id": {"type": "string"}},
                        "required": ["client_id"]
                    }
                },
                {
                    "name": "upsert_client_data",
                    "description": "Create or update a client in the database.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "client_id": {"type": "string"},
                            "business_data": {"type": "string"},
                            "owner_email": {"type": "string"},
                            "is_active": {"type": "boolean"}
                        },
                        "required": ["client_id", "business_data", "owner_email"]
                    }
                }
            ]
        }
    elif method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name")
        args = params.get("arguments", {})
        db = firestore.client()
        
        try:
            if tool_name == "get_client_data":
                doc = db.collection('b2b_clients').document(args["client_id"]).get()
                content = json.dumps(doc.to_dict(), indent=2) if doc.exists else "Client not found."
                response["result"] = {"content": [{"type": "text", "text": content}]}
                
            elif tool_name == "upsert_client_data":
                client_id = args.pop("client_id")
                db.collection('b2b_clients').document(client_id).set(args, merge=True)
                response["result"] = {"content": [{"type": "text", "text": f"Success: {client_id} saved to Firestore."}]}
            else:
                response["error"] = {"code": -32601, "message": "Tool not found"}
        except Exception as e:
            response["error"] = {"code": -32000, "message": str(e)}
    else:
        response["error"] = {"code": -32601, "message": "Method not found"}

    if session_id in mcp_sessions and msg_id is not None:
        mcp_sessions[session_id].put(response)
        resp = Response(status=202)
    else:
        resp = jsonify(response)
        
    resp.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
    resp.headers['Access-Control-Allow-Credentials'] = 'true'
    return resp

# ------------------------------------------
# Dummy OAuth Bypasses for Gemini UI
# ------------------------------------------
@app.route('/oauth/auth', methods=['GET', 'OPTIONS'])
def oauth_auth():
    redirect_uri = request.args.get('redirect_uri')
    state = request.args.get('state')
    return f'<script>window.location.href="{redirect_uri}?code=mcp_bypass_code&state={state}";</script>'

@app.route('/oauth/token', methods=['POST', 'OPTIONS'])
def oauth_token():
    if request.method == 'OPTIONS':
        resp = Response(status=204)
        resp.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return resp
    
    resp = jsonify({"access_token": "mcp_bypass_token", "token_type": "Bearer", "expires_in": 360000})
    resp.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
    return resp
# ==========================================
# 7. ROUTERS
# ==========================================
@app.route('/health', methods=['GET'])
def health():
    return "OK", 200

def get_b2b_client(client_id):
    if not firebase_admin._apps:
        return None
    try:
        db = firestore.client()
        doc = db.collection('b2b_clients').document(client_id).get()
        return doc.to_dict() if doc.exists else None
    except Exception as e:
        print(f"⚠️ Error reading Firestore: {e}")
        return None

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

    business_knowledge = client_data.get('business_data', 'No specific business data provided.')
    commercial_key = client_data.get('openai_api_key')
    owner_email = client_data.get('owner_email') 

    full_system_instruction = f"{BASE_SYSTEM_PROMPT}\n\n--- BUSINESS DATA ---\n{business_knowledge}\n-------------------"

    if commercial_key:
        messages = [{"role": "system", "content": full_system_instruction}]
        for turn in history[-10:]:
            messages.append({"role": turn.get("role", "user"), "content": turn.get("content", "")})
        messages.append({"role": "user", "content": user_message})
        base_stream = stream_openai(messages, commercial_key)
        
    else:
        history_transcript = ""
        temp_history = []
        current_len = len(full_system_instruction) + len(user_message)
        
        for turn in reversed(history):
            role = "Customer" if turn.get("role") == "user" else "Assistant"
            line = f"{role}: {turn.get('content', '')}\n"
            
            if current_len + len(line) > 8500:
                break
                
            temp_history.insert(0, line)
            current_len += len(line)
            
        history_transcript = "".join(temp_history)
        
        # PROMPT INVERSION FIX: System instructions go at the BOTTOM so the AI doesn't forget them.
        full_prompt = (
            f"### Past Conversation:\n{history_transcript}\n\n"
            f"### System Instructions & Data:\n{full_system_instruction}\n\n"
            f"CRITICAL: Respond to the Customer's latest message based ONLY on the Business Data above. DO NOT write the Customer's response.\n"
            f"Customer: {user_message}\nAssistant:"
        )
        base_stream = stream_copilot(full_prompt)

    secure_stream = intercept_leads(base_stream, owner_email, client_id)
    return Response(stream_with_context(secure_stream), mimetype='text/plain')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
