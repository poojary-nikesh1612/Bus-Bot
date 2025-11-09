# 🚌 BABY(Bot-Assisted Bus Yatra)

A smart WhatsApp chatbot using the Google Gemini API and Python (Flask) to provide conversational, real-time bus schedules for Canara Engineering College (CEC).

This bot acts as a "helpful senior" by understanding natural language queries (like "which bus is faster?") and answering with friendly, AI-generated replies.

## ✨ Core Features
* **Conversational AI:** Handles small talk ("hi", "thanks") and general questions about routes.
* **Smart Search:** Understands keywords like "farengipete" and "bc road," not just fixed commands.
* **Hybrid Logic:** Uses a `timetable.json` for 100% factual data and the Gemini API for natural language understanding and friendly replies.
* **Focused Knowledge:** The AI is prompted to stay on-topic and will not answer unrelated questions.

## ⚙️ Tech Stack
* **Backend:** Python 3, Flask
* **AI:** Google Gemini API
* **Messaging:** Meta WhatsApp Cloud API
* **Hosting:** Render
* **Local Testing:** `ngrok`, `dotenv`

## 🚀 How to Run Locally

1.  **Clone the repo:**
    ```bash
    git clone https://github.com/poojary-nikesh1612/Bus-Bot.git
    cd Bus-Bot
    ```

2.  **Set up virtual environment:**
    ```bash
    python -m venv venv
    .\venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Create `.env` file:**
    Create a file named `.env` and add your secret keys:
    ```dotenv
    GEMINI_API_KEY="AIzaSy..."
    META_WA_TOKEN="EAA..."
    META_WA_PHONE_ID="YOUR_TEST_NUMBER_PHONE_ID"
    META_VERIFY_TOKEN="YOUR_SECRET_PASSWORD" 
    ```

5.  **Run the app:**
    ```bash
    python app.py
    ```

6.  **Expose with Ngrok:**
    In a new terminal, run:
    ```bash
    ngrok http 5000
    ```

7.  **Connect Webhook:**
    Paste the `ngrok` URL (e.g., `https://....ngrok-free.app/whatsapp`) into your Meta App's webhook settings.
