import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL = "dcbartolo@gmail.com"

# BizBuySell URL filtered specifically for New Jersey businesses
BIZBUYSELL_NJ_URL = "https://www.bizbuysell.com/new-jersey-businesses-for-sale/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def clean_currency(value_str):
    """Converts '$350,000' or 'N/A' into an integer."""
    if not value_str or "N/A" in value_str or "Undisclosed" in value_str:
        return 0
    cleaned = re.sub(r"[^\d]", "", value_str)
    return int(cleaned) if cleaned else 0

def fetch_and_grade_nj_businesses():
    response = requests.get(BIZBUYSELL_NJ_URL, headers=HEADERS)
    if response.status_code != 200:
        print(f"Failed to fetch page, status code: {response.status_code}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    listings = soup.find_all("div", class_="listing-container")  # BizBuySell listing container
    
    parsed_results = []

    for listing in listings:
        # Title & URL
        title_tag = listing.find("a", class_="title")
        if not title_tag:
            continue
            
        title = title_tag.text.strip()
        link = "https://www.bizbuysell.com" + title_tag["href"] if title_tag["href"].startswith("/") else title_tag["href"]
        
        # Location (e.g., Middlesex County, NJ)
        location_tag = listing.find("span", class_="location")
        location = location_tag.text.strip() if location_tag else "New Jersey"

        # Asking Price & Cash Flow
        price_tag = listing.find("span", class_="price")
        price_str = price_tag.text.strip() if price_tag else "$0"
        price = clean_currency(price_str)

        cash_flow_tag = listing.find("span", class_="cash-flow")
        cash_flow_str = cash_flow_tag.text.strip() if cash_flow_tag else "$0"
        cash_flow = clean_currency(cash_flow_str)

        # Skip non-NJ or invalid listings
        if cash_flow == 0:
            continue

        # Calculate Price-to-SDE Multiple
        multiple = round(price / cash_flow, 2) if cash_flow > 0 else 0

        # --- FINANCIAL GRADING ---
        if cash_flow >= 500000 or (cash_flow >= 350000 and multiple > 0 and multiple <= 2.5):
            grade = "🦄 UNICORN"
        elif cash_flow >= 350000 and multiple <= 3.8:
            grade = "✅ PASS"
        else:
            grade = "❌ FAIL"

        parsed_results.append({
            "title": title,
            "location": location,
            "price": f"${price:,}" if price > 0 else "Undisclosed",
            "cash_flow": f"${cash_flow:,}",
            "raw_cash_flow": cash_flow,
            "multiple": f"{multiple}x" if multiple > 0 else "N/A",
            "grade": grade,
            "url": link
        })

    # Sort so UNICORNs and PASS deals appear at the top
    parsed_results.sort(key=lambda x: x["raw_cash_flow"], reverse=True)
    return parsed_results

def send_daily_email(listings):
    if not listings:
        print("No business listings found matching criteria today.")
        return

    html_body = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <h2>Daily NJ Business Acquisition Report ($350k+ Cash Flow Target)</h2>
        <p>Target Criteria: NJ Location | Minimum SDE/Cash Flow: $350,000/yr</p>
        <hr>
    """

    for item in listings:
        color = "green" if "PASS" in item["grade"] or "UNICORN" in item["grade"] else "red"
        
        html_body += f"""
        <div style="margin-bottom: 20px; padding: 10px; border-left: 5px solid {color}; background-color: #f9f9f9;">
            <h3 style="margin-top: 0;">{item['grade']}: {item['title']}</h3>
            <p style="margin: 4px 0;"><b>Location:</b> {item['location']}</p>
            <p style="margin: 4px 0;"><b>Annual Cash Flow (SDE):</b> <span style="color: green;"><b>{item['cash_flow']}</b></span></p>
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
        print(f"Error sending email: {e}")

if __name__ == "__main__":
    data = fetch_and_grade_nj_businesses()
    send_daily_email(data)
