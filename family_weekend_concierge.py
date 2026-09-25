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
        f"[https://api.open-meteo.com/v1/forecast](https://api.open-meteo.com/v1/forecast)?"
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
    """Safely strips markdown code blocks from model response."""
    text = raw_text.replace("```html", "")
    text = text.replace("```", "")
    return text.strip()

def generate_single_day_itineraries(client, day_name, target_date, weather_info, calendar_info):
    """Generates 5 tailored itineraries for a single day to optimize API performance."""
    prompt = f"""
    You are an expert family activity concierge for a family based in Westfield, NJ (07090).
    
    **DAY CONTEXT:**
    - Target Day: {day_name}, {target_date}
    - Weather Forecast: {weather_info}
    - Google Calendar Commitments:
    {calendar_info}
    
    **WEATHER ALIGNMENT RULES (STRICT):**
    - Evaluate this day's weather forecast ({weather_info}).
    - If rain, high precipitation (>5mm), cold (<50°F), or high winds: ALL 5 itineraries MUST prioritize indoor/covered activities (museums, indoor play places, building/craft centers, indoor railways).
    - If clear/mild/warm: Prioritize outdoor activities (hiking trails, parks, farms, outdoor fairs).
    
    **TIME & SCHEDULE RULES:**
    - Drive time: Max 90 minutes drive from Westfield, NJ.
    - Buffer time: Always add 30 mins after sports or swim lessons for changing/prep before driving or eating.
    - Meal times: Lunch ~12:30 PM, Dinner ~5:30 PM.
    - Return time: Home by ~5:00 PM for dinner, OR home by 8:00-9:00 PM if eating dinner out.
    
    **FAMILY PREFERENCES:**
    - Kids' Interests: Hiking, biking, building, kids museums, play places, dog-friendly spots, rides, exploring new towns, trucks, farms, festivals, crafts, playgrounds.
    - Parents' Preferences: Healthy options, farm-to-table, brewery, unique local spots.
    - Dog-Friendly: Explicitly flag whether the activity is 🐶 Dog Friendly or 🚫 No Dogs Allowed.
    - Restaurants: Secondary to the activity. Do NOT plan a day around a restaurant. Only include if within 30 mins of the activity or on the way home, offering unique/healthy/brewery vibes.
    
    **DELIVERABLE FORMAT:**
    Generate 5 distinct, detailed itinerary options formatted cleanly in semantic HTML inside `<div>` tags:
    For EACH of the 5 options include:
    1. Itinerary Title (e.g. "{day_name} Option 1: Title")
    2. Activity Name & Hyperlinked Website
    3. Cost of Activity
    4. Recommended Stay Duration
    5. Dog-Friendly Flag (🐶 Dog Friendly or 🚫 No Dogs Allowed)
    6. Weather Alignment Note (Explain how it matches {day_name}'s forecast)
    7. Activity Highlights
    8. Full Timeline (accounting for travel from Westfield, calendar events, 30-min buffers, and meal times)
    9. Recommended Nearby Restaurant (Food type, recommended dishes, distance) - ONLY if within 30 mins and worth going to.
    
    Return ONLY valid HTML inside `<div>` tags with clean inline CSS suitable for an email digest. Do NOT wrap in markdown code blocks.
    """
    
    model_name = 'gemini-3.8-flash'
    max_attempts = 5
    
    for attempt in range(max_attempts):
        try:
            print(f"Generating itineraries for {day_name} ({target_date}) - Attempt {attempt + 1}/{max_attempts}...")
            chat = client.chats.create(model=model_name)
            response = chat.send_message(prompt)
            if response and response.text:
                return clean_html_response(response.text)
        except Exception as e:
            err_str = str
