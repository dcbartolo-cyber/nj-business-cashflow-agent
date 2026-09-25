import os
import sys
import time
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from icalendar import Calendar
from google import genai

# --- CONFIGURATION ---
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD")
GCAL_ICS_URL = os.getenv("GCAL_ICS_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RECIPIENT_EMAIL = "dcbartolo@gmail.com"

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
        res = requests.get(url).json()
        daily = res.get("daily", {})
        dates = daily.get("time", [])
        
        sat_str = saturday_date.strftime("%Y-%m-%d")
        sun_str = sunday_date.strftime("%Y-%m-%d")
        
        sat_idx = dates.index(sat_str) if sat_str in dates else None
        sun_idx = dates.index(sun_str) if sun_str in dates else None
        
        sat_weather = f"High {daily['temperature_2m_max'][sat_idx]}°F, Low {daily['temperature_2m_min'][sat_idx]}°F, Precip: {daily['precipitation_sum'][sat_idx]}mm" if sat_idx is not None else "N/A"
        sun_weather = f"High {daily['temperature_2m_max'][sun_idx]}°F, Low {daily['temperature_2m_min'][sun_idx]}°F, Precip: {daily['precipitation_sum'][sun_idx]}mm" if sun_idx is not None else "N/A"
        
        return f"Saturday ({sat_str}): {sat_weather}\nSunday ({sun_str}): {sun_weather}"
    except Exception as e:
        print(f"Weather Fetch Error: {e}")
        return "Weather forecast unavailable."

def fetch_calendar_events(saturday_date, sunday_date):
    """Parses Google Calendar ICS feed for upcoming weekend events."""
    if not GCAL_ICS_URL:
        return "No calendar URL provided."
        
    try:
        res = requests.get(GCAL_ICS_URL)
        cal = Calendar.from_ical(res.content)
        events = []
        
        for component in cal.walk('vevent'):
            start = component.get('dtstart').dt
            summary = str(component.get('summary'))
            
            event_date = start.date() if isinstance(start, datetime) else start
            
            if event_date in [saturday_date, sunday_date]:
                time_str = start.strftime("%I:%M %p") if isinstance(start, datetime) else "All Day"
                events.append(f"- {event_date.strftime('%A')}: '{summary}' at {time_str}")
                
        return "\n".join(events) if events else "No scheduled events on Google Calendar for this weekend."
    except Exception as e:
        print(f"Calendar Fetch Error: {e}")
        return "Unable to parse calendar events."

def generate_itineraries(weather, calendar_events, saturday_date, sunday_date):
    """Uses Gemini API with robust retries for transient 503 server demand."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    You are an expert family activity concierge for a family based in Westfield, NJ (07090).
    
    **UPCOMING WEEKEND CONTEXT:**
    - Saturday Date: {saturday_date}
    - Sunday Date: {sunday_date}
    - Weather Forecast:
    {weather}
    - Existing Google Calendar Commitments:
    {calendar_events}
    
    **TIME & SCHEDULE RULES:**
    - Drive time: Max 90 minutes drive from Westfield, NJ.
    - Buffer time: Always add 30 mins after sports or swim lessons for changing/prep before driving or eating.
    - Meal times: Lunch ~12:30 PM, Dinner ~5:30 PM.
    - Return time: Home by ~5:00 PM for dinner, OR home by 8:00-9:00 PM if eating dinner out.
    - Weather matching: If rain/cold is forecasted, prioritize indoor/covered activities. If clear/warm, prioritize outdoor activities.
    
    **FAMILY PREFERENCES:**
    - Kids' Interests: Hiking, biking, building, kids museums, play places, dog-friendly spots, rides, exploring new towns, trucks, farms, festivals, crafts, playgrounds.
    - Parents' Preferences: Healthy options, farm-to-table, brewery, unique local spots.
    - Dog-Friendly: Explicitly flag whether the activity is 🐶 Dog Friendly or 🚫 No Dogs Allowed.
    - Restaurants: Secondary to the activity. Do NOT plan a day around a restaurant or go out of the way for one. Only include a restaurant if it is within a 30-minute drive of the activity or on the way home, and offers unique, healthy, farm-to-table, or brewery vibes. Otherwise omit.
    
    **DELIVERABLE FORMAT:**
    Generate 5 distinct, detailed itinerary options for the weekend formatted cleanly in semantic HTML.
    For each itinerary include:
    1. Itinerary Title & Day (Saturday or Sunday)
    2. Activity Name & Hyperlinked Website
    3. Cost of Activity
    4. Recommended Stay Duration
    5. Dog-Friendly Flag (🐶 Dog Friendly or 🚫 No Dogs Allowed)
    6. Activity Highlights
    7. Full Timeline (accounting for travel from Westfield, calendar events, 30-min buffers, and meal times)
    8. Recommended Nearby Restaurant (Food type, recommended dishes, distance from activity) - ONLY if within 30 mins and worth going to.
    
    Return ONLY valid HTML inside `<div>` tags with clean inline CSS suitable for an email digest. Do NOT wrap in markdown code blocks.
    """
    
    model_name = 'gemini-3.8-flash'
    max_attempts = 5
    
    for attempt in range(max_attempts):
        try:
            print(f"Attempting generation with model: {model_name} (Attempt {attempt + 1}/{max_attempts})...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            if response and response.text:
                return response.text.replace("```html", "").replace("```", "").strip()
        except Exception as e:
            wait_time = (attempt + 1) * 12  # Progressive 12s, 24s, 36s, 48s backoff
            print(f"Warning: {model_name} returned error: {e}. Waiting {wait_time}s before retrying...")
            time.sleep(wait_time)
                
    raise RuntimeError("Gemini API call failed after multiple retry attempts due to server capacity limits.")

def send_email(html_content):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🗓️ Your Westfield Weekend Family Concierge Digest"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Reply-To"] = RECIPIENT_EMAIL
    
    intro_html = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5; max-width: 700px; margin: auto;">
        <h2>Westfield Weekend Family Concierge</h2>
        <p>Here are your 5 custom-tailored weekend itineraries based on your Google Calendar, live weather, and family preferences.</p>
        <p><i>💡 <b>Feedback Loop:</b> Reply directly to this email with what you picked, loved, or skipped to help refine future suggestions!</i></p>
        <hr style="border: 0; border-top: 1px solid #ccc;">
    """
    
    full_html = intro_html + html_content + "</body></html>"
    msg.attach(MIMEText(full_html, "html"))
    
    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print("Weekend Concierge digest dispatched successfully via Gmail SMTP!")
    except Exception as e:
        print(f"Error sending email: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if not GMAIL_USER or not GMAIL_PASS or not GEMINI_API_KEY:
        print("Error: Missing required secrets (GMAIL_USER, GMAIL_APP_PASSWORD, or GEMINI_API_KEY).")
        sys.exit(1)
        
    sat, sun = get_weekend_dates()
    weather = fetch_weather_forecast(sat, sun)
    events = fetch_calendar_events(sat, sun)
    
    print(f"Fetched Weather:\n{weather}\n")
    print(f"Fetched Calendar Events:\n{events}\n")
    
    itineraries_html = generate_itineraries(weather, events, sat, sun)
    send_email(itineraries_html)
