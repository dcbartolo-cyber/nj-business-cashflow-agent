import os
import re
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL = "dcbartolo@gmail.com"

BIZBUYSELL_NJ_URL = "https://www.bizbuysell.com/new-jersey-businesses-for-sale/"

def clean_currency(value_str):
    """Converts '$350,000' or 'N/A' into an integer."""
    if not value_str or "N/A" in value_str or "Undisclosed" in value_str:
        return 0
    cleaned = re.sub(r"[^\d]", "", value_str)
    return int(cleaned) if cleaned else 0

def grade_business_deal(title, price, cash_flow, location, raw_text):
    """
    Flexible multi-criteria M&A evaluation system.
    Handles incomplete listing data gracefully.
    """
    score = 0
    reasons = []
    text_lower = raw_text.lower()
    
    # 1. Cash Flow Check (Base Requirement)
    if cash_flow >= 500000:
        score += 40
        reasons.append("High Cash Flow ($500k+)")
    elif cash_flow >= 350000:
        score += 25
        reasons.append("Meets $350k Cash Flow Target")
    elif cash_flow > 0:
        reasons.append(f"Cash flow below target (${cash_flow:,})")
    else:
        reasons.append("Financials Undisclosed")

    # 2. Valuation Multiple Check (Price / Cash Flow)
    multiple = round(price / cash_flow, 2) if (cash_flow > 0 and price > 0) else 0
    if 0 < multiple <= 2.5:
        score += 30
        reasons.append(f"Exceptional Valuation ({multiple}x SDE)")
    elif 2.5 < multiple <= 3.8:
        score += 15
        reasons.append(f"Standard Valuation ({multiple}x SDE)")
    elif multiple > 4.5:
        score -= 20
        reasons.append(f"High Multiple ({multiple}x SDE)")

    # 3. Deal Terms & Financing Signals
    if any(k in text_lower for k in ["seller financ", "owner financ", "seller will finance"]):
        score += 15
        reasons.append("Seller Financing Available")
        
    if any(k in text_lower for k in ["sba pre-approved", "sba qualified", "sba eligible", "sba approved"]):
        score += 15
        reasons.append("SBA Pre-Approved")
        
    if any(k in text_lower for k in ["real estate included", "includes real estate", "property included"]):
        score += 20
        reasons.append("Real Estate Included")

    # 4. Operational Stability Signals
    if any(k in text_lower for k in ["absentee", "semi-absentee", "manager in place", "turnkey"]):
        score += 15
        reasons.append("Management in Place / Semi-Absentee")

    # 5. Determine Final Grade
    if cash_flow >= 350000 and score >= 70:
        grade = "🦄 UNICORN"
    elif cash_flow >= 350000 and score >= 35:
        grade = "✅ PASS"
    elif cash_flow == 0 and score >= 30:
        grade = "⚠️ POTENTIAL DEAL (Undisclosed Financials)"
    else:
        grade = "❌ FAIL"

    return grade, score, multiple, reasons

def fetch_and_grade_nj_businesses():
    print("Launching Playwright Headless Chrome Browser...")
    
    parsed_results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        print(f"Navigating to {BIZBUYSELL_NJ_URL}...")
        try:
            page.goto(BIZBUYSELL_NJ_URL, wait_until="domcontentloaded", timeout=60000)
            # Wait for dynamic JS content / listing links to load
            page.wait_for_selector("a.title, div.listing-container", timeout=15000)
        except Exception as e:
            print(f"Notice during page load wait: {e}")

        html_content = page.content()
        browser.close()

    soup = BeautifulSoup(html_content, "html.parser")
    listings = soup.select("div.listing-container, div.bbs-listing, div.diamond")
    
    if not listings:
        print("Standard containers not found, extracting direct listing title elements...")
        listings = soup.find_all("a", class_="title")

    print(f"Extracted {len(listings)} raw listing elements.")

    for listing in listings:
        title_tag = listing.find("a", class_="title") if hasattr(listing, 'find') else None
        if not title_tag and hasattr(listing, 'text') and getattr(listing, 'name', '') == 'a':
            title_tag = listing
            
        if not title_tag:
            continue
            
        title = title_tag.text.strip()
        href = title_tag.get("href", "")
        link = "https://www.bizbuysell.com" + href if href.startswith("/") else href
        
        raw_text = listing.text if hasattr(listing, 'text') else title

        location_tag = listing.find("span", class_="location") if hasattr(listing, 'find') else None
        location = location_tag.text.strip() if location_tag else "New Jersey"

        price_tag = listing.find("span", class_="price") if hasattr(listing, 'find') else None
        price_str = price_tag.text.strip() if price_tag else "$0"
        price = clean_currency(price_str)

        cash_flow_tag = listing.find("span", class_="cash-flow") if hasattr(listing, 'find') else None
        cash_flow_str = cash_flow_tag.text.strip() if cash_flow_tag else "$0"
        cash_flow = clean_currency(cash_flow_str)

        # Grade using multi-criteria engine
        grade, score, multiple, reasons = grade_business_deal(title, price, cash_flow, location, raw_text)

        parsed_results.append({
            "title": title,
            "location": location,
            "price": f"${price:,}" if price > 0 else "Undisclosed",
            "cash_flow": f"${cash_flow:,}" if cash_flow > 0 else "Undisclosed",
            "raw_cash_flow": cash_flow,
            "multiple": f"{multiple}x" if multiple > 0 else "N/A",
            "grade": grade,
            "score": score,
            "reasons": ", ".join(reasons),
            "url": link
        })

    # Priority sorting: UNICORN/PASS first, sorted by cash flow and score
    parsed_results.sort(key=lambda x: (x["raw_cash_flow"], x["score"]), reverse=True)
    return parsed_results

def send_daily_email(listings):
    if not listings:
        print("Error: No listings parsed to send in email.")
        sys.exit(1)

    html_body = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5;">
        <h2>Daily NJ Business Acquisition Digest</h2>
        <p><b>Target Criteria:</b> NJ Location | $350,000+ Annual SDE / EBITDA Target | Multi-Factor M&A Scoring</p>
        <hr>
    """

    for item in listings:
        if "UNICORN" in item["grade"]:
            color = "#6f42c1"  # Purple
        elif "PASS" in item["grade"]:
            color = "#28a745"  # Green
        elif "POTENTIAL" in item["grade"]:
            color = "#fd7e14"  # Orange
        else:
            color = "#dc3545"  # Red
        
        html_body += f"""
        <div style="margin-bottom: 20px; padding: 14px; border-left: 6px solid {color}; background-color: #f8f9fa;">
            <h3 style="margin-top: 0; color: {color};">{item['grade']}: {item['title']}</h3>
            <p style="margin: 4px 0;"><b>Location:</b> {item['location']}</p>
            <p style="margin: 4px 0;"><b>Annual Cash Flow (SDE):</b> <span style="color: #28a745;"><b>{item['cash_flow']}</b></span> | <b>Asking Price:</b> {item['price']} ({item['multiple']})</p>
            <p style="margin: 4px 0;"><b>M&A Quality Score:</b> {item['score']} pts | <b>Key Drivers:</b> <i>{item['reasons']}</i></p>
            <p style="margin: 8px 0 0 0;"><a href="{item['url']}" target="_blank" style="color: #0066cc; font-weight: bold;">View Listing &rarr;</a></p>
        </div>
        """

    html_body += "</body></html>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Daily NJ Business Acquisition Digest ($350k+ Target)"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print("Daily NJ Business report dispatched successfully via Gmail SMTP!")
    except Exception as e:
        print(f"Error sending email via SMTP: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if not GMAIL_USER or not GMAIL_PASS:
        print("Error: GMAIL_USER or GMAIL_APP_PASSWORD secret missing.")
        sys.exit(1)
        
    data = fetch_and_grade_nj_businesses()
    send_daily_email(data)
