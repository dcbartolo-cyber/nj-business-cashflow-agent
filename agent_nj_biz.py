import os
import re
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD")
SCRAPER_KEY = os.getenv("SCRAPERAPI_KEY")
RECIPIENT_EMAIL = "dcbartolo@gmail.com"

BIZBUYSELL_NJ_URL = "https://www.bizbuysell.com/new-jersey-businesses-for-sale/"

def clean_currency(value_str):
    """Converts '$350,000' or 'N/A' into an integer."""
    if not value_str or "N/A" in value_str or "Undisclosed" in value_str:
        return 0
    cleaned = re.sub(r"[^\d]", "", value_str)
    return int(cleaned) if cleaned else 0

def fetch_and_grade_nj_businesses():
    # Pass US country code and JS rendering to solve Cloudflare anti-bot challenges
    scraper_url = "http://api.scraperapi.com"
    params = {
        "api_key": SCRAPER_KEY,
        "url": BIZBUYSELL_NJ_URL,
        "country_code": "us",
        "render": "true"
    }
    
    print("Fetching BizBuySell via ScraperAPI (US Proxy + JS Render)...")
    try:
        response = requests.get(scraper_url, params=params, timeout=60)
        print(f"ScraperAPI HTTP Status Code: {response.status_code}")
    except Exception as e:
        print(f"Connection Error: {e}")
        sys.exit(1)
        
    if response.status_code != 200:
        print(f"Error: ScraperAPI returned HTTP status {response.status_code}")
        print(f"Response Body Snippet: {response.text[:300]}")
        sys.exit(1)

    soup = BeautifulSoup(response.text, "html.parser")
    
    # Try multiple standard BizBuySell listing containers
    listings = soup.select("div.listing-container, div.bbs-listing, div.diamond")
    if not listings:
        print("Notice: Standard containers not matched. Using fallback title selectors...")
        listings = soup.find_all("a", class_="title")
        
    print(f"Successfully extracted {len(listings)} listing elements from BizBuySell.")
    parsed_results = []

    for listing in listings:
        title_tag = listing.find("a", class_="title") if hasattr(listing, 'find') else None
        if not title_tag and hasattr(listing, 'text'):
            title_tag = listing  # Fallback if listing itself is the <a> tag
            
        if not title_tag:
            continue
            
        title = title_tag.text.strip()
        href = title_tag.get("href", "")
        link = "https://www.bizbuysell.com" + href if href.startswith("/") else href
        
        location_tag = listing.find("span", class_="location") if hasattr(listing, 'find') else None
        location = location_tag.text.strip() if location_tag else "New Jersey"

        price_tag = listing.find("span", class_="price") if hasattr(listing, 'find') else None
        price_str = price_tag.text.strip() if price_tag else "$0"
        price = clean_currency(price_str)

        cash_flow_tag = listing.find("span", class_="cash-flow") if hasattr(listing, 'find') else None
        cash_flow_str = cash_flow_tag.text.strip() if cash_flow_tag else "$0"
        cash_flow = clean_currency(cash_flow_str)

        multiple = round(price / cash_flow, 2) if cash_flow > 0 else 0

        # --- FINANCIAL GRADING ---
        if cash_flow >= 500000 or (cash_flow >= 350000 and 0 < multiple <= 2.5):
            grade = "🦄 UNICORN"
        elif cash_flow >= 350000 and multiple <= 3.8:
            grade = "✅ PASS"
        else:
            grade = "❌ FAIL ($350k Target Not Met)"

        parsed_results.append({
            "title": title,
            "location": location,
            "price": f"${price:,}" if price > 0 else "Undisclosed",
            "cash_flow": f"${cash_flow:,}" if cash_flow > 0 else "Undisclosed / N/A",
            "raw_cash_flow": cash_flow,
            "multiple": f"{multiple}x" if multiple > 0 else "N/A",
            "grade": grade,
            "url": link
        })

    parsed_results.sort(key=lambda x: x["raw_cash_flow"], reverse=True)
    return parsed_results

def send_daily_email(listings):
    if not listings:
        print("Error: No listings parsed to send in email.")
        sys.exit(1)

    html_body = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <h2>Daily NJ Business Acquisition Report ($350k+ Cash Flow Target)</h2>
        <p>Target Criteria: NJ Location | Minimum SDE/Cash Flow: $350,000/yr</p>
        <hr>
    """

    for item in listings:
        color = "#28a745" if "PASS" in item["grade"] or "UNICORN" in item["grade"] else "#dc3545"
        
        html_body += f"""
        <div style="margin-bottom: 20px; padding: 12px; border-left: 5px solid {color}; background-color: #f8f9fa;">
            <h3 style="margin-top: 0;">{item['grade']}: {item['title']}</h3>
            <p style="margin: 4px 0;"><b>Location:</b> {item['location']}</p>
            <p style="margin: 4px 0;"><b>Annual Cash Flow (SDE):</b> <span style="color: #28a745;"><b>{item['cash_flow']}</b></span></p>
            <p style="margin: 4px 0;"><b>Asking Price:</b> {item['price']} | <b>SDE Multiple:</b> {item['multiple']}</p>
            <p style="margin: 6px 0;"><a href="{item['url']}" target="_blank" style="color: #0066cc;">View BizBuySell Listing &rarr;</a></p>
        </div>
        """

    html_body += "</body></html>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Daily NJ Business Cash Flow Report ($350k+ Target)"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print("Daily NJ Business report dispatched successfully!")
    except Exception as e:
        print(f"Error sending email via SMTP: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # Diagnostic Secret Checks
    if not GMAIL_USER or not GMAIL_PASS:
        print("Error: GMAIL_USER or GMAIL_APP_PASSWORD secret missing.")
        sys.exit(1)
    if not SCRAPER_KEY:
        print("Error: SCRAPERAPI_KEY secret missing.")
        sys.exit(1)
        
    data = fetch_and_grade_nj_businesses()
    send_daily_email(data)
