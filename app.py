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

# Initialize Firebase securely. If on Render, it uses the environment variable.
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
# 2. SERVER OPTIMIZATIONS (Rate Limits & Keep-Alive)
# ==========================================
request_log = {}
RATE_LIMIT_SECONDS = 3

def is_rate_limited(ip):
    """Prevents spam by limiting IPs. Optimized for Render's free tier RAM limits."""
    now = time.time()
    last_time = request_log.get(ip, 0)
    if now - last_time < RATE_LIMIT_SECONDS:
        return True
    request_log[ip] = now
    
    # Only clean the dictionary if it gets too large to save CPU
    if len(request_log) > 500:
        cleanup_request_log(now)
    return False

def cleanup_request_log(current_time):
    # Create list of old IPs to delete
    to_remove = [ip for ip, t in request_log.items() if current_time - t > 3600]
    for ip in to_remove:
        del request_log[ip]

def keep_alive_worker():
    """Pings the server every 14 mins to prevent Render from sleeping."""
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
Do not make up services, prices, or locations that are not in the Business Data.

LEAD CAPTURE RULES:
1. Be conversational. Ask questions to figure out what specific service the customer needs if they haven't told you yet.
2. Once you know what they need, gently ask for their name and phone number to arrange a callback or quote.
3. As soon as they provide their name and phone number, you MUST output a secret tracking tag summarizing the lead.
   Format the tag EXACTLY like this with pipe symbols: ||LEAD: Name | Phone | Brief summary of what they need||
   Example: ||LEAD: John Doe | 0412 345 678 | Customer has a leaking roof and wants a price estimate||
4. After outputting the tag, warmly thank the customer and tell them the team will call them shortly. Do not mention the tag to the user.
"""

# ==========================================
# 4. AI PROVIDERS (Streaming Functions)
# ==========================================
# (Standard providers omitted for brevity, keeping the main ones used by B2B)
def stream_copilot(message):
    """Connects to free demo AI via websockets."""
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
        except Exception as e:
            q.put(f"[Error: {e}]")
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
    """Commercial AI engine for paid clients."""
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
    except Exception as e:
        yield f"[OpenAI Error: {e}]"

# ==========================================
# 5. LEAD CATCHER ENGINE
# ==========================================
def send_lead_email(owner_email, lead_text, client_id):
    """Sends the intercepted lead to the business owner via Resend."""
    if not owner_email or not resend.api_key:
        return
        
    # Split the tag into Name, Phone, and Summary based on the pipe symbol '|'
    parts = [p.strip() for p in lead_text.split('|')]
    
    # If the AI perfectly followed instructions, we will have 3 parts
    if len(parts) >= 3:
        display_html = f"""
            <p><b>Name:</b> {parts[0]}</p>
            <p><b>Phone:</b> {parts[1]}</p>
            <p><b>Job Details:</b> {parts[2]}</p>
        """
    else:
        # Fallback just in case the AI messed up the formatting
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
    """
    Wraps the AI text stream. Hides ||LEAD|| tags from the user interface 
    and triggers the email dispatch in the background. Safely handles Markdown.
    """
    accumulated = ""
    for chunk in generator:
        accumulated += chunk
        
        if "||" in accumulated:
            if "||LEAD:" in accumulated and accumulated.count("||") >= 2:
                start_idx = accumulated.find("||LEAD:")
                end_idx = accumulated.find("||", start_idx + 2) + 2
                
                tag_string = accumulated[start_idx:end_idx]
                lead_data = tag_string.replace("||LEAD:", "").replace("||", "").strip()
                
                # Fire email in a separate thread so chat doesn't lag
                threading.Thread(target=send_lead_email, args=(owner_email, lead_data, client_id), daemon=True).start()
                
                # Erase tag from stream
                accumulated = accumulated.replace(tag_string, "")
                if accumulated:
                    yield accumulated
                accumulated = ""
                
            # Anti-freeze: If buffer holds normal markdown table pipes but no lead tag, release it
            elif len(accumulated) > 35:
                yield accumulated
                accumulated = ""
        else:
            if accumulated:
                yield accumulated
            accumulated = ""
            
    if accumulated:
        yield re.sub(r'\|\|LEAD:.*?\|\|', '', accumulated)

# ==========================================
# 6. ROUTERS
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

    # Fetch specific business knowledge from Firebase
    client_data = get_b2b_client(client_id)
    if not client_data or not client_data.get('is_active', False):
        return jsonify({"error": "Bot unavailable or inactive"}), 403

    business_knowledge = client_data.get('business_data', 'No specific business data provided.')
    commercial_key = client_data.get('openai_api_key')
    owner_email = client_data.get('owner_email') 

    # Combine Base Rules with Business Knowledge
    full_system_instruction = f"{BASE_SYSTEM_PROMPT}\n\n--- BUSINESS DATA ---\n{business_knowledge}\n-------------------"

    if commercial_key:
        # PAID TIER: OpenAI has higher limits. Pass system instructions normally.
        messages = [{"role": "system", "content": full_system_instruction}]
        for turn in history[-10:]: # Keep last 10 messages
            messages.append({"role": turn.get("role", "user"), "content": turn.get("content", "")})
        messages.append({"role": "user", "content": user_message})
        base_stream = stream_openai(messages, commercial_key)
        
    else:
        # FREE DEMO TIER: Copilot has a strict ~10,000 character limit.
        # We must build the prompt carefully to avoid crashing the model.
        history_transcript = ""
        temp_history = []
        
        # Calculate base lengths
        current_len = len(full_system_instruction) + len(user_message)
        
        # Loop backwards through history so we always keep the newest context first
        for turn in reversed(history):
            role = "Customer" if turn.get("role") == "user" else "Assistant"
            line = f"{role}: {turn.get('content', '')}\n"
            
            # Stop adding history if we cross 8500 chars (leaves 1500 chars breathing room)
            if current_len + len(line) > 8500:
                break
                
            temp_history.insert(0, line) # Insert at front to maintain chronological order
            current_len += len(line)
            
        history_transcript = "".join(temp_history)
        
        full_prompt = (
            f"### System Instructions:\n{full_system_instruction}\n\n"
            f"### Conversation History:\n{history_transcript}\n"
            f"Customer: {user_message}\nAssistant:"
        )
        base_stream = stream_copilot(full_prompt)

    # Wrap the engine stream in the lead catcher
    secure_stream = intercept_leads(base_stream, owner_email, client_id)
    return Response(stream_with_context(secure_stream), mimetype='text/plain')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
