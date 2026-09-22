import os
import re
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from ddgs import DDGS

# --- CONFIGURATION ---
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL = "dcbartolo@gmail.com"

def clean_currency(value_str):
    """Converts '$350,000' or 'N/A' into an integer."""
    if not value_str:
        return 0
    cleaned = re.sub(r"[^\d]", "", value_str)
    return int(cleaned) if cleaned else 0

def extract_financials(snippet):
    """Extracts asking price and cash flow/SDE/EBITDA from listing snippets."""
    price, cash_flow = 0, 0
    
    price_match = re.search(r"(?:Asking Price|Price):\s*\$([\d,]+)", snippet, re.IGNORECASE) or re.search(r"\$([\d,]+)\s*(?:Asking|Price)", snippet, re.IGNORECASE)
    if price_match:
        price = clean_currency(price_match.group(1))

    cf_match = re.search(r"(?:Cash Flow|SDE|EBITDA):\s*\$([\d,]+)", snippet, re.IGNORECASE) or re.search(r"\$([\d,]+)\s*(?:Cash Flow|SDE|EBITDA)", snippet, re.IGNORECASE)
    if cf_match:
        cash_flow = clean_currency(cf_match.group(1))
        
    return price, cash_flow

def grade_business_deal(title, price, cash_flow, snippet):
    """
    Expanded Multi-Factor M&A Scoring Engine.
    Evaluates:
    1. Financial Strength & Valuation
    2. Geographic Scope (NJ & NY Metro Area)
    3. Owner Dependency & Transferability Risk
    4. Revenue Quality & Concentration Signals
    5. Deal Terms, Real Estate & CapEx Quality
    """
    score = 0
    reasons = []
    text_lower = (title + " " + snippet).lower()

    # 1. Geographic Verification (NJ & NY Metro Area)
    geo_keywords = ["new jersey", "nj", "new york", "ny", "westchester", "rockland", "staten island", "long island", "orange county", "tri-state"]
    if any(k in text_lower for k in geo_keywords):
        score += 10
        reasons.append("Target Region (NJ/NY Metro)")

    # 2. Financial Performance (Base Target: $350k+ SDE)
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

    # 3. Valuation Multiple (Price / Cash Flow)
    multiple = round(price / cash_flow, 2) if (cash_flow > 0 and price > 0) else 0
    if 0 < multiple <= 2.5:
        score += 30
        reasons.append(f"Exceptional Valuation ({multiple}x SDE)")
    elif 2.5 < multiple <= 3.8:
        score += 15
        reasons.append(f"Standard Market Multiple ({multiple}x SDE)")
    elif multiple > 4.2:
        score -= 20
        reasons.append(f"High Multiple Penalty ({multiple}x SDE)")

    # 4. Deal Terms & Financing Signals
    if any(k in text_lower for k in ["seller financ", "owner financ", "seller will finance"]):
        score += 15
        reasons.append("Seller Financing Available")
        
    if any(k in text_lower for k in ["sba pre-approved", "sba qualified", "sba eligible", "sba approved"]):
        score += 15
        reasons.append("SBA Pre-Approved")
        
    if any(k in text_lower for k in ["real estate included", "includes real estate", "property included"]):
        score += 20
        reasons.append("Real Estate Included")

    # 5. Owner Dependency & Operational Risk
    if any(k in text_lower for k in ["absentee", "semi-absentee", "manager in place", "turnkey", "key personnel"]):
        score += 15
        reasons.append("Turnkey / Low Owner Dependency")
    if any(k in text_lower for k in ["training provided", "will train", "smooth transition", "seller will stay"]):
        score += 10
        reasons.append("Transition Support Provided")

    # 6. Revenue Quality & Customer Concentration
    if any(k in text_lower for k in ["recurring", "contracted", "b2b", "repeat clients", "loyal customer", "high margin"]):
        score += 15
        reasons.append("Recurring Revenue / High Margin")
    if any(k in text_lower for k in ["no customer concentration", "diversified client", "broad customer base"]):
        score += 10
        reasons.append("Low Customer Concentration Risk")

    # 7. CapEx, Equipment & Asset Quality
    if any(k in text_lower for k in ["equipment included", "fleet included", "fully equipped", "vehicles included", "machinery"]):
        score += 10
        reasons.append("Equipment/Fleet Included (Lower CapEx)")

    # 8. Final Grade Assignment
    if cash_flow >= 350000 and score >= 75:
        grade = "🦄 UNICORN"
    elif cash_flow >= 350000 and score >= 35:
        grade = "✅ PASS"
    elif cash_flow == 0 and score >= 30:
        grade = "⚠️ POTENTIAL DEAL (Undisclosed Financials)"
    else:
        grade = "❌ FAIL"

    return grade, score, multiple, reasons

def fetch_via_ddg():
    queries = [
        'site:bizbuysell.com "New Jersey" "Cash Flow"',
        'site:bizbuysell.com "New York" "Cash Flow"',
        'site:bizbuysell.com "New Jersey" "SDE"',
        'site:bizbuysell.com "New York" "SDE"',
        'site:bizbuysell.com "NY" "Cash Flow"'
    ]
    
    seen_urls = set()
    raw_results = []
    
    ddgs = DDGS()
    
    for query in queries:
        print(f"Querying DuckDuckGo for: {query}")
        try:
            search_results = list(ddgs.text(query, max_results=20))
            print(f"  -> Found {len(search_results)} search hits.")
            for item in search_results:
                link = item.get("href", "")
                if link and link not in seen_urls:
                    seen_urls.add(link)
                    raw_results.append(item)
        except Exception as e:
            print(f"  -> Search Error on query '{query}': {e}")

    print(f"\nTotal unique listings fetched across NY/NJ: {len(raw_results)}")
    
    if not raw_results:
        print("Diagnostic: Search engine returned 0 hits across all queries.")
        return []

    parsed_results = []
    for item in raw_results:
        title = item.get("title", "")
        link = item.get("href", "")
        snippet = item.get("body", "")

        price, cash_flow = extract_financials(snippet)
        grade, score, multiple, reasons = grade_business_deal(title, price, cash_flow, snippet)

        print(f"Diagnostic Item: '{title[:40]}...' | Price: ${price:,} | Cash Flow: ${cash_flow:,} | Score: {score} | Grade: {grade}")

        parsed_results.append({
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

    parsed_results.sort(key=lambda x: (x["raw_cash_flow"], x["score"]), reverse=True)
    return parsed_results

def send_daily_email(listings):
    if not listings:
        print("No listings retrieved today to include in email.")
        return

    html_body = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5;">
        <h2>Daily NY/NJ Business Acquisition Digest</h2>
        <p><b>Target Scope:</b> NJ & NY Metro Area | $350,000+ Annual SDE Target | Expanded M&A Criteria</p>
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
            <p style="margin: 4px 0;"><b>M&A Quality Score:</b> {item['score']} pts | <b>Key Signals:</b> <i>{item['reasons']}</i></p>
            <p style="margin: 8px 0 0 0;"><a href="{item['url']}" target="_blank" style="color: #0066cc; font-weight: bold;">View Listing &rarr;</a></p>
        </div>
        """

    html_body += "</body></html>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Daily NY/NJ Business Acquisition Digest ($350k+ Target)"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print("Daily NY/NJ Business report dispatched successfully via Gmail SMTP!")
    except Exception as e:
        print(f"Error sending email via SMTP: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if not GMAIL_USER or not GMAIL_PASS:
        print("Error: GMAIL_USER or GMAIL_APP_PASSWORD secret missing.")
        sys.exit(1)
        
    data = fetch_via_ddg()
    send_daily_email(data)
