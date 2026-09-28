import os
import re
import sys
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google import genai
from google.genai import types

# --- CONFIGURATION ---
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", GMAIL_USER)

def generate_skills_games_content():
    """Generates 3 SEL and 3 Developmental games for 5yo & 3yo brothers using Gemini API."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = (
        "You are an expert pediatric occupational therapist and child development specialist.\n"
        "Generate a fun, warm, engaging weekend playbook for parents of two young brothers (aged 5 and 3).\n\n"
        "GAME REQUIREMENTS:\n"
        "- Zero to minimal prep: Playable at home with ordinary items/toys, in the car, or at the dinner table.\n"
        "- High engagement: Games must feel like pure fun and laughter, never like a lesson.\n\n"
        "CONTENT BREAKDOWN:\n"
        "1. SECTION 1: 3 Social & Emotional Learning (SEL) Games\n"
        "   - Target hidden skills: Kindness/Gratitude, Sharing/Turn-taking, and Calm Bodies/Self-Regulation.\n"
        "   - Disguised as play so the kids don't realize they're learning.\n"
        "2. SECTION 2: 3 General Developmental Skills Games\n"
        "   - Target skills: Fine Motor, Working Memory/Auditory Processing, and Executive Function/Impulse Control.\n"
        "   - Perfectly tailored to the joint play dynamic of a 5-year-old and a 3-year-old.\n\n"
        "FOR EVERY GAME INCLUDE:\n"
        "- Game Name & Setting (e.g., 🚗 Car, 🍽️ Dinner Table, 🛋️ Living Room)\n"
        "- Quick Setup & How to Play (1-2 short paragraphs or bullet points)\n"
        "- The Hidden Skill Taught (Name the specific skill)\n"
        "- Why It's Valuable (2-3 sentences on the neurological/developmental benefit for ages 3 and 5)\n\n"
        "FORMATTING:\n"
        "Return ONLY clean, valid HTML inside <div> tags using inline CSS suitable for an email digest. "
        "Use friendly headers, subtle background cards (#f8f9fa), and clear bolding. Do NOT wrap in markdown code fences."
    )
    
    model_name = 'gemini-3.8-flash'
    max_attempts = 5
    
    for attempt in range(max_attempts):
        try:
            print(f"Generating Skills Games Playbook (Attempt {attempt + 1}/{max_attempts})...")
            chat = client.chats.create(model=model_name)
            response = chat.send_message(prompt)
            if response and response.text:
                clean_text = re.sub(r"```(?:html)?", "", response.text).strip()
                return clean_text
        except Exception as e:
            err_str = str(e)
            print(f"Warning attempt {attempt + 1}: {err_str}")
            wait_time = (attempt + 1) * 15
            time.sleep(wait_time)
            
    raise RuntimeError("Failed to generate skills games playbook after multiple retries.")

def send_email(html_content):
    """Dispatches the weekly play digest via Gmail SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🎲 Your Weekend Playbook: 6 Quick Skills Games for the Boys"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Reply-To"] = RECIPIENT_EMAIL
    
    plain_text = "Your Thursday Skills & Play Guide is ready! Please view in an HTML-compatible email client."
    intro_html = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5; max-width: 700px; margin: auto;">
        <h2 style="color: #2c3e50; border-bottom: 2px solid #e74c3c; padding-bottom: 5px;">
            🎲 Weekend Skills & Play Playbook
        </h2>
        <p>Here are 6 quick, zero-prep games designed to build social, emotional, and cognitive strength through play with your 5- and 3-year-old this weekend!</p>
        <hr style="border: 0; border-top: 1px solid #ccc; margin-bottom: 25px;">
    """
    
    full_html = intro_html + html_content + "</body></html>"
    
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(full_html, "html"))
    
    try:
        print(f"Connecting to Gmail SMTP to deliver play digest to {RECIPIENT_EMAIL}...")
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print(f"SUCCESS: Skills Games digest dispatched to {RECIPIENT_EMAIL}!")
    except Exception as e:
        print(f"Error sending email: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if not GMAIL_USER or not GMAIL_PASS or not GEMINI_API_KEY:
        print("Error: Missing required secrets (GMAIL_USER, GMAIL_APP_PASSWORD, or GEMINI_API_KEY).")
        sys.exit(1)
        
    games_html = generate_skills_games_content()
    send_email(games_html)
