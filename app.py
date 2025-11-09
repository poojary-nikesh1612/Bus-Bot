import os
import json
import requests
import threading
from flask import Flask, request, jsonify
from datetime import datetime
import google.generativeai as genai
import pytz  # <-- 1. IMPORT PTYZ
from dotenv import load_dotenv

load_dotenv()

# --- 1. CONFIGURATION ---
app = Flask(__name__)

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
META_WA_TOKEN = os.environ.get('META_WA_TOKEN')
META_WA_PHONE_ID = os.environ.get('META_WA_PHONE_ID')
META_VERIFY_TOKEN = os.environ.get('META_VERIFY_TOKEN')
IST = pytz.timezone('Asia/Kolkata')  # <-- 2. DEFINE IST TIMEZONE

try:
    genai.configure(api_key=GEMINI_API_KEY)
    print("Gemini client configured.")
except Exception as e:
    print(f"Error configuring Gemini: {e}")

# --- 2. META WEBHOOK VERIFICATION (GET REQUEST) ---
@app.route("/whatsapp", methods=["GET"])
def verify_webhook():
    # (This function is unchanged)
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == META_VERIFY_TOKEN:
        print("WEBHOOK_VERIFIED")
        return challenge, 200
    else:
        print("WEBHOOK_VERIFICATION_FAILED")
        return "Verification failed", 403

# --- 3. THE "WORKER" FUNCTION (Handles all logic) ---
def process_bot_logic(from_number, msg_body):
    # (This function is unchanged, but the logic it calls is now fixed)
    print(f"WORKER: Processing message for {from_number}: {msg_body}")
    
    bot_reply = "" 

    try:
        entities = extract_entities_with_gemini(msg_body)
        intent = entities.get('intent')
        
        if intent == 'time_query':
            search_term = entities.get('search_term')
            time = entities.get('target_time')
            bus_data = get_bus_info(search_term, time)
            status = bus_data.get("status")
            
            if status == "found":
                bot_reply = generate_friendly_reply(bus_data)
            
            elif status == "too_late":
                last_bus_time_12hr = datetime.strptime(bus_data.get('last_bus_time'), '%H:%M').strftime('%I:%M %p')
                target_time_12hr = datetime.strptime(time, '%H:%M').strftime('%I:%M %p')
                bot_reply = f"Sorry, I couldn't find any buses for *{search_term}* after *{target_time_12hr}*.\n\nIt looks like the last bus of the day was at *{last_bus_time_12hr}*."

            elif status == "not_found":
                t_time_12hr = datetime.strptime(time, '%H:%M').strftime('%I:%M %p')
                contact = bus_data.get('contact')
                reply_parts = [
                    f"Sorry, I couldn't find any buses for *{search_term}* after *{t_time_12hr}*.",
                    f"{bus_data.get('off_peak_message')}\n",
                    f"It's best to call the conductor to check: `{contact}`"
                ]
                bot_reply = "\n".join(reply_parts)
            
            elif status == "no_route":
                bot_reply = f"Sorry, I don't have any schedules for *'{entities.get('search_term')}'*. I only know about 'Mangalore', 'BC Road', and 'Farengipete'."
            
            else: 
                bot_reply = "Sorry, something went wrong. Please try that again."
        
        elif intent == 'general_question' or intent == 'chat':
            bot_reply = generate_qa_reply(msg_body)
            
        else:
            bot_reply = "Sorry, I'm not sure how to help with that. I'm best at finding bus times."

        send_whatsapp_message(from_number, bot_reply)

    except Exception as e:
        print(f"!!! WORKER THREAD FAILED: {e} !!!")
        send_whatsapp_message(from_number, "Oh no! My brain just glitched. Please try asking me again.")

# --- 4. RECEIVE MESSAGES (POST REQUEST) - THE "CONTROLLER" ---
@app.route("/whatsapp", methods=["POST"])
def receive_message():
    # (This function is unchanged)
    try:
        body = request.get_json()
        if body.get("object") and body.get("entry"):
            changes = body["entry"][0].get("changes", [])
            if changes:
                value = changes[0].get("value", {})
                if value.get("messages"):
                    message = value["messages"][0]
                    from_number = message["from"]
                    msg_body = message["text"]["body"]
                    
                    worker_thread = threading.Thread(
                        target=process_bot_logic,
                        args=(from_number, msg_body)
                    )
                    worker_thread.start()
                    
                    return "OK", 200

        return "OK", 200
        
    except Exception as e:
        print(f"Error in receive_message (main): {e}")
        return "Error", 500

# --- 5. GEMINI BRAIN 1 (NLP Entity Extraction) ---
def extract_entities_with_gemini(user_message):
    """
    NEW: This function now uses the IST timezone.
    """
    
    # --- 3. GET THE CURRENT TIME IN IST ---
    current_time_str = datetime.now(IST).strftime('%H:%M')
    
    system_prompt = f"""
    You are an expert entity extraction model.
    Your job is to analyze the user's message and return a JSON object.
    The current time is {current_time_str}.

    You must determine one of three "intents":
    1.  `time_query`: The user is asking for a bus at a specific time. (e.g., "bus to bc road now", "4pm mangalore bus")
    2.  `general_question`: The user is asking a question *about* the buses. (e.g., "which bus is faster?", "how much is the fare?", "who are you?")
    3.  `chat`: The user is just making small talk. (e.g., "hi", "thanks", "ok")

    Based on the intent, return a JSON object:
    
    - If "intent" is `time_query`:
      {{
        "intent": "time_query",
        "search_term": "[destination keyword]",
        "target_time": "[HH:MM 24-hour time]"
      }}
      (If user says "now" or no time, use current time: {current_time_str})

    - If "intent" is `general_question` or `chat`:
      {{
        "intent": "general_question",
        "search_term": null,
        "target_time": null
      }}

    User Message: "{user_message}"
    """
    
    generation_config = {"response_mime_type": "application/json"}
    
    model = genai.GenerativeModel('gemini-2.5-flash',
                                  generation_config=generation_config)
    
    try:
        response = model.generate_content(system_prompt)
        raw_response_text = response.text
        
        print(f"Gemini Raw Response: {raw_response_text}")

        json_start = raw_response_text.find('{')
        json_end = raw_response_text.rfind('}') + 1
        
        if json_start == -1 or json_end == 0:
            raise ValueError(f"No JSON object found: {raw_response_text}")
            
        clean_json_str = raw_response_text[json_start:json_end]
        print(f"Gemini Cleaned JSON: {clean_json_str}")
        
        return json.loads(clean_json_str)
        
    except Exception as e:
        print(f"Gemini API error: {e}")
        return {"intent": "chat", "search_term": None, "target_time": None}

# --- 6. FACTUAL BRAIN (Bus Logic) ---
def get_bus_info(search_term, target_time_str):
    # (This function is unchanged)
    try:
        with open('timetable.json', 'r') as f:
            timetable = json.load(f)
    except Exception as e:
        print(f"CRITICAL ERROR loading timetable.json: {e}")
        return {"status": "error", "message": "Sorry, my schedule file is broken."}

    if not search_term or not target_time_str:
        return {"status": "not_understood"}

    try:
        target_time = datetime.strptime(target_time_str, '%H:%M').time()
    except ValueError:
        return {"status": "not_understood"}

    found_route = None
    for route in timetable['routes']:
        if search_term.lower() in route['keywords']:
            found_route = route
            break 
    
    if not found_route:
        return {"status": "no_route", "search_term": search_term}

    all_buses_today = []
    if found_route['service_type'] == 'Fixed':
        all_buses_today.extend(found_route.get('schedule', []))
    if found_route['service_type'] == 'Variable':
        for period in found_route.get('peak_schedule', {}):
            all_buses_today.extend(found_route['peak_schedule'][period])
            
    if not all_buses_today:
        return {"status": "not_found", "search_term": search_term, "target_time": target_time_str, **found_route}

    last_bus_str = all_buses_today[-1].split(' ')[0]
    last_bus_time = datetime.strptime(last_bus_str, '%H:%M').time()

    next_buses_list = []
    for time_str in all_buses_today:
        bus_time = datetime.strptime(time_str.split(' ')[0], '%H:%M').time()
        if bus_time >= target_time:
            next_buses_list.append(time_str)
    
    if next_buses_list:
        return {
            "status": "found",
            "destination": found_route['name'],
            "target_time": target_time_str,
            "buses": next_buses_list,
            **found_route 
        }
    else:
        if target_time > last_bus_time:
            return {
                "status": "too_late",
                "search_term": search_term,
                "target_time": target_time_str,
                "last_bus_time": last_bus_str,
                **found_route
            }
        else:
            return {
                "status": "not_found",
                "search_term": search_term,
                "target_time": target_time_str,
                **found_route
            }

# --- 7. GEMINI BRAIN 2 (Friendly Reply Writer) ---
def generate_friendly_reply(bus_data):
    # (This function is unchanged)
    bus_data_json = json.dumps(bus_data, indent=2)
    
    system_prompt = f"""
    You are a friendly and helpful bus assistant...
    ...
    RULES FOR THE REPLY:
    ...
    8.  ***TIME FORMAT RULE (CRITICAL):***
        * You **MUST** convert all 24-hour times...
    ---
    
    Write the final reply. Do not include the JSON.
    """
    
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(system_prompt)
        
        reply_text = response.text.replace("```", "")
        
        print(f"Gemini Friendly Reply: {reply_text}")
        return reply_text
    
    except Exception as e:
        print(f"Gemini (Writer) API error: {e}")
        return json.dumps(bus_data, indent=2) 

# --- 8. GEMINI BRAIN 3 (The "Helpful Senior" QA) --- 
def generate_qa_reply(user_message):
    """
    NEW: This function now knows the bus fare.
    """
    
    system_prompt = f"""
    You are "Baby" (B.A.B.Y. = Benjanapadavu Area Bus Yatra), a helpful bus bot for Canara Engineering College (CEC).
    You are NOT just a database. You are a "helpful senior" with all the local knowledge.

    YOUR KNOWLEDGE BASE:
    - You know the schedules for two main routes: Mangalore and BC Road.
    - The *Rajkumar* bus is the FAST route to Mangalore (via Farengipete).
    - The *Rajalaxmi* bus is the SLOW route (via Nermarga/Polali).
    
    - --- NEW FARE INFO ---
    - The fare to BC Road is about *₹10 for students* and *₹20 for normal people*.
    - The Mangalore fare is similar.
    - --- END NEW FARE INFO ---
    
    - The buses are private, not government.
    - Your job is to answer bus *time* questions, but also *general questions*.
    
    YOUR TASK:
    A user just sent a message that is NOT a time query.
    
    RULES:
    1.  Be friendly, conversational, and helpful. Use emojis.
    2.  Keep your reply short (2-3 sentences).
    3.  If they say "hi", "hello", etc., greet them back.
    4.  If they say "thanks", "thank you", etc., say "You're welcome! Happy to help! 😊"
    5.  If they ask a *question* (like "which bus is faster?" or "how much is the fare?"), answer it using your KNOWLEDGE BASE.
    6.  If you *don't* know the answer, just say "Sorry, I'm not sure about that! I'm best at finding bus times."
    7.  If you greet them, gently remind them what you do. (e.g., "Hey there! I'm Baby, the CEC bus bot. Ask me for bus times!")
    
    User's message: "{user_message}"
    
    Write a friendly, short reply.
    """
    
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(system_prompt)
        
        reply_text = response.text.replace("```", "")
        
        print(f"Gemini Chat Reply: {reply_text}")
        return reply_text
    
    except Exception as e:
        print(f"Gemini (Chat) API error: {e}")
        return "Sorry, I'm not sure how to reply to that! I'm best at finding bus times."

# --- 9. SEND MESSAGE FUNCTION (Talks to Meta) ---
def send_whatsapp_message(to_number, message_text):
    # (This function is unchanged)
    if not message_text:
        print("ERROR: Tried to send an empty message.")
        return

    url = f"https://graph.facebook.com/v18.0/{META_WA_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_WA_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "text": {
            "body": message_text,
            "preview_url": False 
        }
    }
    
    response = None
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        print(f"Message sent to {to_number}: {response.json()}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending message: {e}")
        if response is not None:
            print(f"Response body: {response.text}")

# --- 10. RUN THE SERVER ---
if __name__ == '__main__':
    print("Database features removed. Running in simple mode.")
    
    port = int(os.environ.get("PORT", 5000))
    app.run(port=port, host='0.0.0.0', debug=True)