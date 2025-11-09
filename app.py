import os
import json
import requests
import threading
from flask import Flask, request, jsonify
from datetime import datetime
import google.generativeai as genai
import pytz  
from dotenv import load_dotenv

load_dotenv()


app = Flask(__name__)

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
META_WA_TOKEN = os.environ.get('META_WA_TOKEN')
META_WA_PHONE_ID = os.environ.get('META_WA_PHONE_ID')
META_VERIFY_TOKEN = os.environ.get('META_VERIFY_TOKEN')
IST = pytz.timezone('Asia/Kolkata') 

try:
    genai.configure(api_key=GEMINI_API_KEY)
    print("Gemini client configured.")
except Exception as e:
    print(f"Error configuring Gemini: {e}")

#META WEBHOOK VERIFICATION (GET REQUEST) 
@app.route("/whatsapp", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == META_VERIFY_TOKEN:
        print("WEBHOOK_VERIFIED")
        return challenge, 200
    else:
        print("WEBHOOK_VERIFICATION_FAILED")
        return "Verification failed", 403

# THE "WORKER" FUNCTION (Handles all logic)
def process_bot_logic(from_number, msg_body):
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
            
            elif status == "no_route":
                bot_reply = f"Sorry, I don't have any schedules for *'{search_term}'*. I only know about the Mangalore and BC Road buses that pass via Nermarga and Farengipete."
            
            else: 
                bot_reply = "Sorry, something went wrong. Please try that again."
        
        elif intent == 'chat':
            bot_reply = generate_chat_reply(msg_body) 
            
        else:
            bot_reply = "Sorry, I'm not sure how to help with that. I'm best at finding bus times."

        send_whatsapp_message(from_number, bot_reply)

    except Exception as e:
        print(f"!!! WORKER THREAD FAILED: {e} !!!")
        send_whatsapp_message(from_number, "Oh no! My brain just glitched. Please try asking me again.")

#RECEIVE MESSAGES (POST REQUEST) - THE "CONTROLLER" 
@app.route("/whatsapp", methods=["POST"])
def receive_message():
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

# GEMINI BRAIN 1 (NLP Entity Extraction) 
def extract_entities_with_gemini(user_message):
  
    current_time_str = datetime.now(IST).strftime('%H:%M')
    
    system_prompt = f"""
    You are an expert entity extraction model.
    Your job is to analyze the user's message and return a JSON object.
    The current time is {current_time_str}.

    You must determine one of two "intents":
    1.  `time_query`: The user is asking for a bus time. (e.g., "bus to bc road now", "4pm mangalore bus", "farengipete")
    2.  `chat`: The user is just making small talk or asking a question I can't answer. (e.g., "hi", "thanks", "what is the fare?", "welcome aboard","best fast route")

    Based on the intent, return a JSON object:
    
    - If "intent" is `time_query`:
      {{
        "intent": "time_query",
        "search_term": "[destination keyword]",
        "target_time": "[HH:MM 24-hour time]"
      }}
      (If user says "now" or no time, use current time: {current_time_str})
      (The search_term MUST be a location, like 'mangalore', 'bc road', 'farengipete', 'nermarga')

    - If "intent" is `chat`:
      {{
        "intent": "chat",
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

#FACTUAL BRAIN (Bus Logic) 
def get_bus_info(search_term, target_time_str):
    
    try:
        with open('timetable.json', 'r') as f:
            timetable = json.load(f)
    except Exception as e:
        print(f"CRITICAL ERROR loading timetable.json: {e}")
        return {"status": "error"}

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

    
    def find_next_buses(schedule, target):
        next_buses = []
        for time_str in schedule:
            bus_time = datetime.strptime(time_str.split(' ')[0], '%H:%M').time()
            if bus_time >= target:
                next_buses.append(time_str)
        return next_buses

    college_buses = find_next_buses(found_route.get('college_stand_schedule', []), target_time)
    main_stand_buses = find_next_buses(found_route.get('main_stand_schedule', []), target_time)
    

    if college_buses or main_stand_buses:
        return {
            "status": "found",
            "destination": found_route['name'],
            "target_time": target_time_str,
            "college_buses": college_buses,
            "main_stand_buses": main_stand_buses,
            "note": found_route.get('note', ''),
            "contact": found_route.get('contact', None),
            "service_type": found_route.get('service_type')
        }
    else:
        return {
            "status": "found", 
            "destination": found_route['name'],
            "target_time": target_time_str,
            "college_buses": [], 
            "main_stand_buses": [],
            "note": found_route.get('note', ''),
            "contact": found_route.get('contact', None),
            "service_type": found_route.get('service_type')
        }

# GEMINI BRAIN 2 (Friendly Reply Writer)
def generate_friendly_reply(bus_data):
    
    bus_data_json = json.dumps(bus_data, indent=2)
    
    system_prompt = f"""
    You are "Baby" (B.A.B.Y.), a friendly bus assistant for Canara Engineering College.
    Your job is to write a clear, helpful, and concise reply based on the data I provide.

    HERE IS THE BUS DATA:
    {bus_data_json}

    ---
    YOUR TASK:
    Write a friendly, formatted reply for the student.

    RULES FOR THE REPLY:
    1.  Be conversational and friendly.
    2.  Use WhatsApp formatting (*bold*, _italics_, ```monospace```).
    3.  **TIME FORMAT (CRITICAL):** Convert ALL 24-hour times (e.g., '16:30', '08:05') into a friendly 12-hour format (e.g., '4:30 PM', '8:05 AM').
    4.  If a bus time has "(Sometimes)", add a ⚠️ warning emoji.
    5.  Start by confirming their request (e.g., "Hey there! Looking for buses to [Destination] around [Time]?").

    ***LOGIC FOR BUS LISTS (VERY IMPORTANT):***
    
    * **Case 1: BOTH lists have buses.**
        * First, list the `college_buses` under a heading like "✅ *At the College Stand*".
        * Second, list the `main_stand_buses` under a heading like "🚶 *At the Benjanapadavu bus Stand (1km walk)*".
    
    * **Case 2: ONLY `college_buses` are found.**
        * Just list them. Don't mention the main stand.
    
    * **Case 3: ONLY `main_stand_buses` are found.**
        * State clearly that no buses are coming to the college.
        * List the `main_stand_buses` under the "🚶 *At the Benjanapadavu bus Stand (1km walk)*" heading.
    
    * **Case 4: BOTH lists are EMPTY.**
        * State that you couldn't find any more buses for that route today.
        * Show the "note" from the data, as it's the last piece of info.

    ***PHONE NUMBER RULE:***
    * **ONLY** show the 'contact' number if the `service_type` is `Variable` OR if any bus time has `(Sometimes)` in it. Do not show it for "Fixed" buses.
    ---
    
    Write the final reply which need to be original and friendly wihtout any non-sense, unwanted characters. Do not include the JSON.
    """
    
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(system_prompt)
        
        reply_text = response.text.replace("```", "")
        
        print(f"Gemini Friendly Reply: {reply_text}")
        return reply_text
    
    except Exception as e:
        print(f"Gemini (Writer) API error: {e}")
        # Fallback in case AI fails
        return "Sorry, I found the bus info but had trouble writing the reply. Please try again."

#GEMINI BRAIN 3 (Small Talk)
def generate_chat_reply(user_message):

    system_prompt = f"""
    You are "Baby" (B.A.B.Y. = Bot-Assisted Bus Yatra), a helpful bus bot for Canara Engineering College (CEC).
    You are NOT just a database. You are a "helpful senior" with all the local knowledge.

    YOUR KNOWLEDGE BASE:
    - You know the schedules for two main routes: Mangalore and BC Road.
    - The *Rajkumar* bus is the FAST route to Mangalore. It takes the highway via Farengipete.
    - The *Rajalaxmi* bus is the SLOW route. It goes through Nermarga.
    - The fare is cheap ₹10 for bc road for students and for mangalore maybe ₹15-₹30, but you don't know the exact price.
    - The buses are private, not government. They don't have AC.
    - Your job is to answer bus *time* questions, but also *general questions* about the buses.
    
    YOUR TASK:
    A user just sent a message that is NOT a time query. It's either small talk (hi, thanks) or a general question (which bus is faster?).
    
    RULES:
    1.  Be friendly, conversational, and helpful. Use emojis.
    2.  Keep your reply short (2-3 sentences).
    3.  If they say "hi", "hello", etc., greet them back.
    4.  If they say "thanks", "thank you", etc., say "You're welcome! Happy to help!, 😊"
    5.  If they ask a *question* (like "which bus is faster?" or "who are you?"), answer it using your KNOWLEDGE BASE.
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

    

# SEND MESSAGE FUNCTION (Talks to Meta)
def send_whatsapp_message(to_number, message_text):
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