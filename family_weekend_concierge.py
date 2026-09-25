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
        res = requests.get(url).json()
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
        res = requests.get(GCAL_ICS_URL)
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
    """Safely strips markdown code blocks using regex to avoid quote line-wrapping bugs."""
    clean_text = re.sub(r"```(?:html)?", "", raw_text)
    return clean_text.strip()

def generate_single_day_itineraries(client, day_name, target_date, weather_info, calendar_info):
    """Generates 5 tailored itineraries using direct generate_content for fast, reliable delivery."""
    prompt = (
        f"You are an expert family activity concierge for a family based in Westfield, NJ (07090).\n\n"
        f"**DAY CONTEXT:**\n"
        f"- Target Day: {day_name}, {target_date}\n"
        f"- Weather Forecast: {weather_info}\n"
        f"- Google Calendar Commitments:\n{calendar_info}\n\n"
        f"**WEATHER ALIGNMENT RULES (STRICT):**\n"
        f"- Evaluate weather forecast ({weather_info}).\n"
        f"- If rain, high precipitation (>5mm), cold (<50°F), or high winds: ALL 5 itineraries MUST prioritize indoor/covered activities.\n"
        f"- If clear/mild/warm: Prioritize outdoor activities.\n\n"
        f"**TIME & SCHEDULE RULES:**\n"
        f"- Drive time: Max 90 minutes drive from Westfield, NJ.\n"
        f"- Buffer time: Always add 30 mins after sports or swim lessons.\n"
        f"- Meal times: Lunch ~12:30 PM, Dinner ~5:30 PM.\n"
        f"- Return time: Home by ~5:00 PM for dinner, OR home by 8:00-9:00 PM if eating dinner out.\n\n"
        f"**FAMILY PREFERENCES:**\n"
        f"- Kids: Hiking, biking, building, kids museums, play places, dog-friendly spots, rides, exploring new towns, trucks, farms, festivals, crafts, playgrounds.\n"
        f"- Parents: Healthy options, farm-to-table, brewery, unique local spots.\n"
        f"- Dog-Friendly: Explicitly flag 🐶 Dog Friendly or 🚫 No Dogs Allowed.\n"
        f"- Restaurants: Secondary to activity. Only include if within 30 mins, offering unique/healthy/brewery vibes.\n\n"
        f"**DELIVERABLE FORMAT:**\n"
        f"Generate 5 distinct, compact itinerary options in clean semantic HTML inside <div> tags.\n"
        f"Keep HTML lightweight and concise without redundant inline CSS or fluff.\n"
        f"For EACH option include:\n"
        f"1. Title (e.g. '{day_name} Option 1: Title')\n"
        f"2. Activity Name & Hyperlinked Website\n"
        f"3. Cost & Stay Duration\n"
        f"4. Dog-Friendly Flag\n"
        f"5. Weather Alignment Note\n"
        f"6. Activity Highlights\n"
        f"7. Full Timeline\n"
        f"8. Nearby Restaurant (if within 30 mins)\n\n"
        f"Return ONLY valid HTML inside <div> tags. Do NOT wrap in markdown code blocks."
    )
    
    model_name = 'gemini-3.8-flash'
    max_attempts = 6
    
    for attempt in range(max_attempts):
        try:
            print(f"Generating itineraries for {day_name} ({target_date}) - Attempt {attempt + 1}/{max_attempts}...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            if response and response.text:
                return clean_html_response(response.text)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                match = re.search(r'retry in (\d+\.?\d*)s', err_str, re.IGNORECASE) or re.search(r"'retryDelay': '(\d+)s'", err_str)
                wait_time = (int(float(match.group(1))) + 5) if match else 30
                print(f"Quota rate limit hit (429). Waiting {wait_time}s...")
            else:
                wait_time = min((attempt + 1) * 10, 40)
                print(f"Warning: {model_name} returned error: {e}. Retrying in {wait_time}s...")
            
            time.sleep(wait_time)
            
    err_msg = f"Failed to generate itineraries for {day_name} after {max_attempts} attempts."
    raise RuntimeError(err_msg)

def generate_full_weekend_digest(sat_date, sun_date, sat_weather, sun_weather, sat_events, sun_events):
    """Executes separate Saturday and Sunday requests with a pacing pause."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # 1. Generate Saturday Options
    sat_html = generate_single_day_itineraries(client, "Saturday", sat_date, sat_weather, sat_events)
    
    # Pause 5 seconds to pace requests
    print("Saturday complete! Pausing 5 seconds before Sunday generation...")
    time.sleep(5)
    
    # 2. Generate Sunday Options
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
    
    plain_text_summary = "Your Westfield Weekend Family Concierge Digest is ready. Please view this email in an HTML-compatible email client to see your 10 custom itineraries."
    intro_html = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5; max-width: 700px; margin: auto;">
        <h2>Westfield Weekend Family Concierge</h2>
        <p>Here are your 10 custom-tailored weekend itineraries (5 for Saturday and 5 for Sunday), each strictly aligned to that day's live weather forecast, Google Calendar commitments, and family preferences.</p>
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
