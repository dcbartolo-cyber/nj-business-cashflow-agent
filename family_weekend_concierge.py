import os
import json
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

def fetch_itineraries_json(sat_date, sun_date, sat_weather, sun_weather, sat_events, sun_events):
    """Generates structured JSON for both Saturday and Sunday in 1 fast API request."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    You are an expert family concierge for a family in Westfield, NJ (07090).
    
    WEEKEND DETAILS:
    - Saturday ({sat_date}): Weather={sat_weather} | Calendar={sat_events}
    - Sunday ({sun_date}): Weather={sun_weather} | Calendar={sun_events}
    
    RULES:
    1. Max 90 mins drive from Westfield, NJ.
    2. Weather Alignment: If precip > 5mm or cold (<50F), MUST pick indoor/covered activities. If clear/warm, pick outdoor.
    3. Kids: Hiking, biking, building, museums, play places, trucks, farms, playgrounds. Parents: Healthy, farm-to-table, brewery, local.
    4. Dog-Friendly: Flag "🐶 Dog Friendly" or "🚫 No Dogs Allowed".
    
    OUTPUT FORMAT:
    Return ONLY a single valid JSON object structured as:
    {{
      "saturday": [
        {{
          "title": "Option Title",
          "activity_name": "Activity Name",
          "website": "https://...",
          "cost": "Cost details",
          "duration": "Stay duration",
          "dog_friendly": "🐶 Dog Friendly",
          "weather_note": "Why it fits Saturday weather",
          "highlights": ["Highlight 1", "Highlight 2"],
          "timeline": "Full schedule string",
          "restaurant": "Nearby dining option or N/A"
        }}
      ],
      "sunday": [ ... 5 options formatted same way ... ]
    }}
    """
    
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.2
    )
    
    print("Sending single JSON generation request to Gemini...")
    response = client.models.generate_content(
        model='gemini-3.8-flash',
        contents=prompt,
        config=config
    )
    
    if response and response.text:
        return json.loads(response.text)
    raise RuntimeError("Failed to receive JSON payload from Gemini API.")

def build_email_html(data, sat_date, sun_date, sat_weather, sun_weather):
    """Converts structured JSON directly into clean, responsive HTML locally in Python."""
    
    def render_day_block(day_name, day_date, weather_str, options):
        html = f"""
        <div style="margin-bottom: 30px;">
            <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px;">🗓️ {day_name} Options ({day_date})</h2>
            <p style="background: #f8f9fa; padding: 10px; border-radius: 5px;"><b>Forecast:</b> {weather_str}</p>
        """
        for idx, item in enumerate(options, 1):
            web_link = f'<a href="{item.get("website", "#")}" target="_blank">{item.get("activity_name", "Website")}</a>'
            highlights_list = "".join([f"<li>{h}</li>" for h in item.get("highlights", [])])
            
            html += f"""
            <div style="background: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 15px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                <h3 style="color: #2c3e50; margin-top: 0;">{idx}. {item.get('title', 'Option')}</h3>
                <p><b>Activity:</b> {web_link} | <b>Cost:</b> {item.get('cost', 'N/A')} | <b>Stay:</b> {item.get('duration', 'N/A')}</p>
                <p><b>Dog Status:</b> {item.get('dog_friendly', 'N/A')} | <i>{item.get('weather_note', '')}</i></p>
                <p><b>Highlights:</b></p>
                <ul>{highlights_list}</ul>
                <p><b>Schedule Timeline:</b> {item.get('timeline', 'N/A')}</p>
                <p><b>Recommended Nearby Dining:</b> {item.get('restaurant', 'N/A')}</p>
            </div>
            """
        html += "</div>"
        return html

    sat_html = render_day_block("Saturday", sat_date, sat_weather, data.get("saturday", []))
    sun_html = render_day_block("Sunday", sun_date, sun_weather, data.get("sunday", []))
    
    return f"{sat_html}<hr style='border:0; border-top:2px solid #ccc; margin:30px 0;'>{sun_html}"

def send_email(html_content):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🗓️ Your Westfield Weekend Family Concierge Digest"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Reply-To"] = RECIPIENT_EMAIL
    
    plain_text = "Your Westfield Weekend Concierge Digest is ready. Please view in an HTML-compatible email client."
    intro_html = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5; max-width: 700px; margin: auto;">
        <h2>Westfield Weekend Family Concierge</h2>
        <p>Here are your 10 custom-tailored weekend itineraries (5 for Saturday and 5 for Sunday), aligned to live weather and family preferences.</p>
        <p><i>💡 <b>Feedback Loop:</b> Reply directly to this email with what you picked, loved, or skipped to help refine future suggestions!</i></p>
        <hr style="border: 0; border-top: 1px solid #ccc;">
    """
    
    full_html = intro_html + html_content + "</body></html>"
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(full_html, "html"))
    
    print(f"Connecting to Gmail SMTP to deliver digest to {RECIPIENT_EMAIL}...")
    server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
    server.login(GMAIL_USER, GMAIL_PASS)
    server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
    server.quit()
    print(f"SUCCESS: Weekend Concierge digest dispatched to {RECIPIENT_EMAIL}!")

if __name__ == "__main__":
    if not GMAIL_USER or not GMAIL_PASS or not GEMINI_API_KEY:
        print("Error: Missing required secrets.")
        sys.exit(1)
        
    sat_date, sun_date = get_weekend_dates()
    sat_weather, sun_weather = fetch_weather_forecast(sat_date, sun_date)
    sat_events, sun_events = fetch_calendar_events(sat_date, sun_date)
    
    print(f"Saturday Weather: {sat_weather}")
    print(f"Sunday Weather: {sun_weather}\n")
    
    itinerary_data = fetch_itineraries_json(sat_date, sun_date, sat_weather, sun_weather, sat_events, sun_events)
    email_html = build_email_html(itinerary_data, sat_date, sun_date, sat_weather, sun_weather)
    send_email(email_html)
