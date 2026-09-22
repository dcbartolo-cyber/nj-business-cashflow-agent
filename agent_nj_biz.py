import os
import re
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests

# --- CONFIGURATION ---
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD")
GOOGLE_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")
RECIPIENT_EMAIL = "dcbartolo@gmail.com"

def clean_currency(value_str):
    """Converts '$350,000' or 'N/A' into an integer."""
    if not value_str:
        return 0
    cleaned = re.sub(r"[^\d]", "", value_str)
    return int(cleaned) if cleaned else 0

def extract_financials(snippet):
    """Extracts asking price and cash flow from listing text/snippets."""
    price, cash_flow = 0, 0
    
    # Extract Price
    price_match = re.search(r"Asking Price:\s*\$([\d,]+)", snippet, re.IGNORECASE) or re.search(r"\$([\d,]+)\s*(?:Price|Asking)", snippet, re.IGNORECASE)
    if price_match:
        price = clean_currency(price_match.group(1))

    # Extract Cash Flow / SDE
    cf_match = re.search(r"Cash Flow:\s*\$([\d,]+)", snippet, re.IGNORECASE) or re.search(r"SDE:\s*\$([\d,]+)", snippet, re.IGNORECASE)
    if cf_match:
        cash_flow = clean_currency(cf_match.group(1))
        
    return price, cash_flow

def grade_business_deal(title, price, cash_flow, snippet):
    """
    Flexible Multi-Criteria M&A Evaluation Engine.
    Evaluates financial strength, valuation, deal structure, and operational quality.
    """
    score = 0
    reasons = []
    text_lower = (title + " " + snippet).lower()

    # 1. Financial Strength (Base Criterion)
    if cash_flow >= 500000:
        score += 40
        reasons.append("High SDE ($500k+)")
    elif cash_flow >= 350000:
        score += 25
        reasons.append("Meets $350k SDE Target")
    elif cash_flow > 0:
        reasons.append(f"SDE below target (${cash_flow:,})")
    else:
        reasons.append("SDE Undisclosed in Snippet")

    # 2. Valuation Multiple (Price / Cash Flow)
    multiple = round(price / cash_flow, 2) if (cash_flow > 0 and price > 0) else 0
    if 0 < multiple <= 2.5:
        score += 30
        reasons.append(f"Exceptional Valuation ({multiple}x SDE)")
    elif 2.5 < multiple <= 3.8:
        score += 15
        reasons.append(f"Standard Market Multiple ({multiple}x SDE)")
    elif multiple > 4.2:
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

    # 4. Operational & Quality Signals
    if any(k in text_lower for k in ["absentee", "semi-absentee", "manager in place", "turnkey"]):
        score += 15
        reasons.append("Management in Place / Semi-Absentee")

    if any(k in text_lower for k in ["recurring", "contracted", "b2b", "high margin"]):
        score += 10
        reasons.append("Recurring Revenue / High Margin")

    # 5. Final Grade Assignment
    if cash_flow >= 350000 and score >= 65:
        grade = "🦄 UNICORN"
    elif cash_flow >= 350000 and score >= 30:
        grade = "✅ PASS"
    elif cash_flow == 0 and score >= 25:
        grade = "⚠️ POTENTIAL DEAL (Undisclosed Financials)"
    else:
        grade = "❌ FAIL"

    return grade, score, multiple, reasons

def fetch_via_google_search():
    query = 'site:bizbuysell.com/Business-Opportunity "New Jersey" "$350,000" OR "$400,000" OR "$500,000"'
    url = f"https://www.googleapis.com/customsearch/v1?key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}&q={query}"
    
    print("Querying Google Custom Search API for NJ BizBuySell listings...")
    res = requests.get(url)
    
    if res.status_code != 200:
        print(f"Google API Error: {res.status_code} - {res.text}")
        sys.exit(1)
        
    items = res.json().get("items", [])
    print(f"Retrieved {len(items)} listings from Google Index.")
    
    results = []
    for item in items:
        title = item.get("title", "")
        link = item.get("link", "")
        snippet = item.get("snippet", "")
        
        price, cash_flow = extract_financials(snippet)
        grade, score, multiple, reasons = grade_business_deal(title, price, cash_flow, snippet)
        
        results.append({
            "title": title.replace(" - BizBuySell", ""),
            "price": f"${price:,}" if price > 0 else "Undisclosed",
            "cash_flow": f"${cash_flow:,}" if cash_flow > 0 else "Undisclosed",
            "raw_cash_flow": cash_flow,
            "multiple": f"{multiple}x" if multiple > 0 else "N/A",
            "grade": grade,
            "score": score,
            "reasons": ", ".join(reasons),
            "url": link
        })
        
    results.sort(key=lambda x: (x["raw_cash_flow"], x["score"]), reverse=True)
    return results

def send_daily_email(listings):
    if not listings:
        print("No listings found today.")
        return

    html_body = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5;">
        <h2>Daily NJ Business Acquisition Digest</h2>
        <p><b>Criteria:</b> NJ Location | $350,000+ Annual SDE Target | Multi-Factor M&A Scoring</p>
        <hr>
    """

    for item in listings:
        if "UNICORN" in item["grade"]:
            color = "#6f42c1" # Purple
        elif "PASS" in item["grade"]:
            color = "#28a745" # Green
        elif "POTENTIAL" in item["grade"]:
            color = "#fd7e14" # Orange
        else:
            color = "#dc3545" # Red
        
        html_body += f"""
        <div style="margin-bottom: 20px; padding: 14px; border-left: 6px solid {color}; background-color: #f8f9fa;">
            <h3 style="margin-top: 0; color: {color};">{item['grade']}: {item['title']}</h3>
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
    if not GMAIL_USER or not GMAIL_PASS or not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        print("Error: Missing environment variables/secrets.")
        sys.exit(1)
        
    data = fetch_via_google_search()
    send_daily_email(data)
