import os
import re
import sys
import time
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from icalendar import Calendar
from google import genai
from google.genai import types

# --- CONFIGURATION ---
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD")
GCAL_ICS_URL = os.getenv("GCAL_ICS_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", GMAIL_USER)

# Westfield, NJ Coordinates
WESTFIELD_LAT = 40.6584
WESTFIELD_LON = -74.3475

def get_weekend_dates():
    """Returns upcoming Saturday and Sunday date objects."""
    today = datetime.now()
    days_until_saturday = (5 - today.weekday()) % 7
    saturday = today + timedelta(days=days_until_saturday)
    sunday = saturday + timedelta(days=1)
    return saturday.date(), sunday.date()

def fetch_weather_forecast(saturday_date, sunday_date):
    """Fetches Westfield, NJ weekend weather in Fahrenheit via Open-Meteo API."""
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={WESTFIELD_LAT}&longitude={WESTFIELD_LON}"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode"
        f"&temperature_unit=fahrenheit&timezone=America%2FNew_York&forecast_days=10"
    )
    try:
        res = requests.get(url, timeout=10).json()
        daily = res.get("daily", {})
        dates = daily.get("time", [])
        
        sat_str = saturday_date.strftime("%Y-%m-%d")
        sun_str = sunday_date.strftime("%Y-%m-%d")
        
        sat_idx = dates.index(sat_str) if sat_str in dates else None
        sun_idx = dates.index(sun_str) if sun_str in dates else None
        
        sat_weather = f"High {daily['temperature_2m_max'][sat_idx]}°F, Low {daily['temperature_2m_min'][sat_idx]}°F, Precip: {daily['precipitation_sum'][sat_idx]}mm" if sat_idx is not None else "N/A"
        sun_weather = f"High {daily['temperature_2m_max'][sun_idx]}°F, Low {daily['temperature_2m_min'][sun_idx]}°F, Precip: {daily['precipitation_sum'][sun_idx]}mm" if sun_idx is not None else "N/A"
        
        return sat_weather, sun_weather
    except Exception as e:
        print(f"Weather Fetch Error: {e}")
        return "Weather forecast unavailable.", "Weather forecast unavailable."

def fetch_calendar_events(saturday_date, sunday_date):
    """Parses Google Calendar ICS feed for upcoming weekend events."""
    if not GCAL_ICS_URL:
        return "No calendar URL provided.", "No calendar URL provided."
        
    try:
        res = requests.get(GCAL_ICS_URL, timeout=10)
        cal = Calendar.from_ical(res.content)
        sat_events, sun_events = [], []
        
        for component in cal.walk('vevent'):
            start = component.get('dtstart').dt
            summary = str(component.get('summary'))
            event_date = start.date() if isinstance(start, datetime) else start
            time_str = start.strftime("%I:%M %p") if isinstance(start, datetime) else "All Day"
            
            if event_date == saturday_date:
                sat_events.append(f"- '{summary}' at {time_str}")
            elif event_date == sunday_date:
                sun_events.append(f"- '{summary}' at {time_str}")
                
        sat_str = "\n".join(sat_events) if sat_events else "No scheduled events on Google Calendar."
        sun_str = "\n".join(sun_events) if sun_events else "No scheduled events on Google Calendar."
        return sat_str, sun_str
    except Exception as e:
        print(f"Calendar Fetch Error: {e}")
        return "Unable to parse calendar events.", "Unable to parse calendar events."

def clean_html_response(raw_text):
    """Safely strips markdown code blocks using regex."""
    clean_text = re.sub(r"```(?:html)?", "", raw_text)
    return clean_text.strip()

def generate_single_day_itineraries(client, day_name, target_date, weather_info, calendar_info):
    """Generates 5 tailored itineraries using low-temperature direct generation."""
    prompt = (
        f"You are a family concierge for a family in Westfield, NJ (07090).\n"
        f"Date: {day_name}, {target_date}\n"
        f"Weather: {weather_info}\n"
        f"Calendar: {calendar_info}\n\n"
        f"STRICT RULES:\n"
        f"1. Drive time: Max 90 mins from Westfield, NJ.\n"
        f"2. Weather alignment: If precip > 5mm or cold (<50F), MUST select indoor/covered activities. If clear/warm, select outdoor.\n"
        f"3. Family: Kids like hiking, biking, building, museums, play places, trucks, farms, playgrounds. Parents like healthy, farm-to-table, brewery, local spots.\n"
        f"4. Flag 🐶 Dog Friendly or 🚫 No Dogs Allowed.\n\n"
        f"OUTPUT FORMAT:\n"
        f"Provide 5 concise options in clean semantic HTML (using <div>, <h3>, <p>, <ul>, <li>).\n"
        f"Keep text brief and compact to ensure fast response.\n"
        f"For each option include:\n"
        f"- Title & Website Link\n"
        f"- Cost & Stay Duration\n"
        f"- Dog Friendly Flag & Weather Alignment Note\n"
        f"- Quick Highlights (bullet points)\n"
        f"- Compact Timeline (Travel from Westfield, lunch ~12:30, home by ~5 PM or ~8 PM)\n"
        f"- Nearby Restaurant (within 30 mins, optional)\n\n"
        f"Return ONLY valid HTML inside <div> tags. Do NOT wrap in markdown code blocks."
    )
    
    model_name = 'gemini-3.8-flash'
    max_attempts = 5
    
    config = types.GenerateContentConfig(
        temperature=0.3,
        max_output_tokens=2500
    )
    
    for attempt in range(max_attempts):
        try:
            print(f"Generating itineraries for {day_name} ({target_date}) - Attempt {attempt + 1}/{max_attempts}...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )
            if response and response.text:
                return clean_html_response(response.text)
        except Exception as e:
            err_str = str(e)
            print(f"Warning attempt {attempt + 1}: {err_str}")
            wait_time = 25 * (attempt + 1)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                match = re.search(r'retry in (\d+\.?\d*)s', err_str, re.IGNORECASE) or re.search(r"'retryDelay': '(\d+)s'", err_str)
                wait_time = (int(float(match.group(1))) + 5) if match else 45
                print(f"Quota rate limit hit (429). Pausing {wait_time}s...")
            else:
                print(f"Server capacity demand spike (503). Pausing {wait_time}s before retry...")
            
            time.sleep(wait_time)
            
    err_msg = f"Failed to generate itineraries for {day_name} after {max_attempts} attempts."
    raise RuntimeError(err_msg)

def generate_full_weekend_digest(sat_date, sun_date, sat_weather, sun_weather, sat_events, sun_events):
    """Executes separate Saturday and Sunday requests with a pacing pause."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    sat_html = generate_single_day_itineraries(client, "Saturday", sat_date, sat_weather, sat_events)
    
    print("Saturday generation complete! Pausing 10 seconds before Sunday generation...")
    time.sleep(10)
    
    sun_html = generate_single_day_itineraries(client, "Sunday", sun_date, sun_weather, sun_events)
    
    combined_html = f"""
    <div style="margin-bottom: 30px;">
        <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px;">🗓️ Saturday Options ({sat_date})</h2>
        <p><b>Forecast:</b> {sat_weather}</p>
        {sat_html}
    </div>
    <hr style="border: 0; border-top: 2px solid #eee; margin: 40px 0;">
    <div style="margin-bottom: 30px;">
        <h2 style="color: #2c3e50; border-bottom: 2px solid #2ecc71; padding-bottom: 5px;">🗓️ Sunday Options ({sun_date})</h2>
        <p><b>Forecast:</b> {sun_weather}</p>
        {sun_html}
    </div>
    """
    return combined_html

def send_email(html_content):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🗓️ Your Westfield Weekend Family Concierge Digest (Weather-Aligned)"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Reply-To"] = RECIPIENT_EMAIL
    
    plain_text_summary = "Your Westfield Weekend Family Concierge Digest is ready. Please view in an HTML-compatible email client."
    intro_html = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5; max-width: 700px; margin: auto;">
        <h2>Westfield Weekend Family Concierge</h2>
        <p>Here are your 10 custom-tailored weekend itineraries (5 for Saturday and 5 for Sunday), aligned to live weather and family preferences.</p>
        <p><i>💡 <b>Feedback Loop:</b> Reply directly to this email with what you picked, loved, or skipped to help refine future suggestions!</i></p>
        <hr style="border: 0; border-top: 1px solid #ccc;">
    """
    
    full_html = intro_html + html_content + "</body></html>"
    
    msg.attach(MIMEText(plain_text_summary, "plain"))
    msg.attach(MIMEText(full_html, "html"))
    
    try:
        print(f"Connecting to Gmail SMTP to deliver digest to {RECIPIENT_EMAIL}...")
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print(f"SUCCESS: Weekend Concierge digest dispatched to {RECIPIENT_EMAIL}!")
    except Exception as e:
        print(f"Error sending email: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if not GMAIL_USER or not GMAIL_PASS or not GEMINI_API_KEY:
        print("Error: Missing required secrets (GMAIL_USER, GMAIL_APP_PASSWORD, or GEMINI_API_KEY).")
        sys.exit(1)
        
    sat_date, sun_date = get_weekend_dates()
    sat_weather, sun_weather = fetch_weather_forecast(sat_date, sun_date)
    sat_events, sun_events = fetch_calendar_events(sat_date, sun_date)
    
    print(f"Saturday Weather: {sat_weather}")
    print(f"Sunday Weather: {sun_weather}\n")
    
    itineraries_html = generate_full_weekend_digest(sat_date, sun_date, sat_weather, sun_weather, sat_events, sun_events)
    send_email(itineraries_html)
