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

# Underwriting Assumptions (6% LMA / Mortgage Rate)
INTEREST_RATE = 0.06        # 6.0% Implied LMA Rate
DOWN_PAYMENT_PCT = 0.20     # 20% Down Payment
LOAN_TERM_YEARS = 30

# Explicit terms that indicate a listing is NOT actively for sale
EXCLUDE_TERMS = [
    "recently sold", "sold on", "sold for", "sold date", "last sold",
    "off market", "off-market", "no longer for sale", "pending", 
    "under contract", "contingent", "zestimate", "redfin estimate", 
    "tax assessment", "closed sale"
]

# Required terms to confirm the property is an ACTIVE listing
ACTIVE_INDICATORS = [
    "for sale", "asking price", "asking $", "listed for", "listed at", 
    "price:", "active listing", "mls#", "for-sale", "homes for sale"
]

def clean_currency(value_str):
    """Converts '$450,000' or 'N/A' into an integer."""
    if not value_str:
        return 0
    cleaned = re.sub(r"[^\d]", "", value_str)
    return int(cleaned) if cleaned else 0

def calculate_monthly_mortgage(principal, annual_rate=0.06, years=30):
    """Calculates monthly principal + interest payment at 6% interest."""
    if principal <= 0:
        return 0
    monthly_rate = annual_rate / 12
    num_payments = years * 12
    monthly_payment = principal * (monthly_rate * (1 + monthly_rate)**num_payments) / ((1 + monthly_rate)**num_payments - 1)
    return round(monthly_payment, 2)

def is_truly_active_listing(title, snippet, url):
    """
    Strict 3-layer filter to guarantee property is currently active:
    1. Rejects URLs containing /sold/ or /recently-sold/
    2. Rejects snippets containing historical sold/off-market keywords
    3. Requires at least one positive 'for sale' / active indicator
    """
    text_lower = (title + " " + snippet).lower()
    url_lower = url.lower()

    # Rule 1: URL Check
    if "/sold/" in url_lower or "/recently-sold/" in url_lower:
        return False

    # Rule 2: Negative Keyword Exclusion
    # (Excludes 'sold' unless it's part of 'sold as-is' or 'being sold')
    for term in EXCLUDE_TERMS:
        if term in text_lower:
            return False
            
    # Check for standalone "sold" if not accompanied by "as is" or "as-is"
    if "sold" in text_lower and "sold as" not in text_lower and "sold as-is" not in text_lower:
        return False

    # Rule 3: Must contain a positive active signal
    has_active_signal = any(indicator in text_lower for indicator in ACTIVE_INDICATORS) or "for-sale" in url_lower
    return has_active_signal

def extract_property_details(snippet, title):
    """Extracts asking price and estimated rent/revenue."""
    combined_text = title + " " + snippet
    
    # Ignore prices preceded by "Sold for" or "Zestimate"
    price_match = re.search(r"(?:Asking Price|Price|Listed at|For Sale):\s*\$([\d,]+)", combined_text, re.IGNORECASE)
    if not price_match:
        price_match = re.search(r"\$([\d,]+)", combined_text)
        
    price = clean_currency(price_match.group(1)) if price_match else 0
    
    rent_match = re.search(r"(?:Rent|Est\. Rent|Income|Gross):\s*\$([\d,]+)", combined_text, re.IGNORECASE)
    monthly_gross = clean_currency(rent_match.group(1)) if rent_match else 0
    
    # If no rent is specified, apply 1.1% monthly gross revenue proxy
    if monthly_gross == 0 and price > 0:
        monthly_gross = int(price * 0.011)
        
    return price, monthly_gross

def evaluate_investment_property(title, price, monthly_gross, snippet):
    """
    Evaluates real estate properties using a 6% LMA financing model and $1,000/mo cash flow floor.
    """
    if price <= 0:
        return "❌ FAIL", 0, 0, 0, 0, "Undisclosed Price"

    # Debt Service @ 6% Rate (20% Down)
    loan_amount = price * (1 - DOWN_PAYMENT_PCT)
    monthly_piti_debt = calculate_monthly_mortgage(loan_amount, INTEREST_RATE, LOAN_TERM_YEARS)
    
    # Operating Expenses (Est. 35% of Gross for Taxes, Insurance, PM, Vacancy, CapEx)
    est_operating_expenses = monthly_gross * 0.35
    net_operating_income_monthly = monthly_gross - est_operating_expenses
    
    # Net Monthly Cash Flow post-debt service
    net_monthly_cash_flow = net_operating_income_monthly - monthly_piti_debt
    annual_cash_flow = net_monthly_cash_flow * 12
    down_payment = price * DOWN_PAYMENT_PCT
    coc_return = round((annual_cash_flow / down_payment) * 100, 1) if down_payment > 0 else 0

    score = 0
    reasons = []
    text_lower = (title + " " + snippet).lower()

    # --- PILLAR 1: CASH FLOW & YIELD ---
    if net_monthly_cash_flow >= 1500:
        score += 40
        reasons.append(f"High Cash Flow (${net_monthly_cash_flow:,.0f}/mo)")
    elif net_monthly_cash_flow >= 1000:
        score += 25
        reasons.append(f"Meets $1k/mo Cash Flow Target (${net_monthly_cash_flow:,.0f}/mo)")
    else:
        reasons.append(f"Cash flow below target (${net_monthly_cash_flow:,.0f}/mo)")

    # --- PILLAR 2: STRATEGY & REGIONAL FIT ---
    if any(k in text_lower for k in ["lake norman", "poconos", "catskills", "gulf shores", "branson", "waterfront", "beach", "cabin"]):
        score += 20
        reasons.append("High STR / Vacation Hub Signal")
        if any(k in text_lower for k in ["cost seg", "furnished", "superhost", "airbnb"]):
            score += 15
            reasons.append("STR Tax Loophole Eligible")

    if any(k in text_lower for k in ["charlotte", "houston", "medical center", "corporate rental", "travel nurse", "mid term", "30 day"]):
        score += 15
        reasons.append("High MTR / Corporate Housing Potential")

    if any(k in text_lower for k in ["columbus", "huntsville", "duplex", "triplex", "quadplex", "multi family", "multifamily"]):
        score += 20
        reasons.append("Multi-Family LTR (High Rent Stability)")

    # --- PILLAR 3: FINANCING & VALUE-ADD ---
    if any(k in text_lower for k in ["seller financing", "owner financing", "assumable"]):
        score += 15
        reasons.append("Favorable Seller Financing")

    # --- FINAL GRADE ASSIGNMENT ---
    if net_monthly_cash_flow >= 1000 and score >= 55:
        grade = "🦄 UNICORN DEAL"
    elif net_monthly_cash_flow >= 1000 and score >= 25:
        grade = "✅ PASS"
    elif net_monthly_cash_flow < 1000 and score >= 35:
        grade = "⚠️ VALUE-ADD POTENTIAL"
    else:
        grade = "❌ FAIL"

    return grade, score, net_monthly_cash_flow, coc_return, monthly_piti_debt, ", ".join(reasons)

def fetch_top_real_estate():
    # Targeted queries focusing on active inventory endpoints
    queries = [
        # Charlotte & NC Metro Active Multi-Family
        'site:redfin.com/county/2055/NC/Iredell-County "for sale" "multi family" OR "duplex"',
        'site:realtor.com/realestateandhomes-detail "Charlotte" "NC" "for sale" "duplex" OR "triplex"',
        'site:redfin.com/city/38491/NC/Lake-Norman-of-Iredell "for sale" "waterfront" OR "cabin"',
        # Sunbelt & Midwest Cash Flow Hubs (Active LTR & MTR)
        'site:realtor.com/realestateandhomes-detail "Columbus" "OH" "for sale" "duplex" OR "triplex"',
        'site:realtor.com/realestateandhomes-detail "Huntsville" "AL" "for sale" "multi family"',
        'site:redfin.com/city/8903/TX/Houston "for sale" "Medical Center" "furnished"',
        # Top Vacation / STR Drive-To Markets (Active)
        'site:realtor.com/realestateandhomes-detail "Poconos" "PA" "for sale" "turnkey" OR "cabin"',
        'site:redfin.com/city/8172/AL/Gulf-Shores "for sale" "condo" OR "beach"'
    ]
    
    seen_urls = set()
    raw_results = []
    ddgs = DDGS()
    
    for query in queries:
        print(f"Querying DuckDuckGo for active listings: {query}")
        try:
            results = list(ddgs.text(query, max_results=15))
            for item in results:
                link = item.get("href", "")
                title = item.get("title", "")
                snippet = item.get("body", "")

                if link and link not in seen_urls:
                    # Enforce strict active listing verification
                    if is_truly_active_listing(title, snippet, link):
                        seen_urls.add(link)
                        raw_results.append(item)
                    else:
                        print(f"  [Filtered Out Off-Market/Sold]: {title[:55]}...")
        except Exception as e:
            print(f"Search Error on query '{query}': {e}")

    print(f"\nTotal VERIFIED ACTIVE properties retrieved: {len(raw_results)}")

    parsed_properties = []
    for item in raw_results:
        title = item.get("title", "")
        link = item.get("href", "")
        snippet = item.get("body", "")

        price, monthly_gross = extract_property_details(snippet, title)
        grade, score, cash_flow, coc, debt_service, reasons = evaluate_investment_property(title, price, monthly_gross, snippet)

        parsed_properties.append({
            "title": title,
            "price": f"${price:,}" if price > 0 else "Undisclosed",
            "monthly_gross": f"${monthly_gross:,}/mo",
            "cash_flow": f"${cash_flow:,.0f}/mo",
            "raw_cash_flow": cash_flow,
            "debt_service": f"${debt_service:,.0f}/mo",
            "coc_return": f"{coc}%",
            "grade": grade,
            "score": score,
            "reasons": reasons,
            "url": link
        })

    parsed_properties.sort(key=lambda x: (x["raw_cash_flow"], x["score"]), reverse=True)
    return parsed_properties

def send_daily_email(properties):
    if not properties:
        print("No active property listings retrieved today.")
        return

    html_body = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5;">
        <h2>Daily Active Investment Real Estate Digest</h2>
        <p><b>Underwriting Model:</b> 6% Implied LMA Rate (20% Down, 30Yr Amort) | <b>Target:</b> $1,000+/mo Net Cash Flow</p>
        <hr>
    """

    for item in properties:
        if "UNICORN" in item["grade"]:
            color = "#6f42c1"
        elif "PASS" in item["grade"]:
            color = "#28a745"
        elif "VALUE-ADD" in item["grade"]:
            color = "#fd7e14"
        else:
            color = "#dc3545"
        
        html_body += f"""
        <div style="margin-bottom: 20px; padding: 14px; border-left: 6px solid {color}; background-color: #f8f9fa;">
            <h3 style="margin-top: 0; color: {color};">{item['grade']}: {item['title']}</h3>
            <p style="margin: 4px 0;"><b>Price:</b> {item['price']} | <b>Est. Debt Service (6%):</b> {item['debt_service']}</p>
            <p style="margin: 4px 0;"><b>Net Cash Flow:</b> <span style="color: #28a745;"><b>{item['cash_flow']}</b></span> | <b>CoC Return:</b> {item['coc_return']}</p>
            <p style="margin: 4px 0;"><b>Investment Score:</b> {item['score']} pts | <b>Highlights:</b> <i>{item['reasons']}</i></p>
            <p style="margin: 8px 0 0 0;"><a href="{item['url']}" target="_blank" style="color: #0066cc; font-weight: bold;">View Listing &rarr;</a></p>
        </div>
        """

    html_body += "</body></html>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Daily Active Real Estate Digest ($1,000+/mo Target @ 6% LMA)"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print("Daily Real Estate Digest dispatched successfully via Gmail SMTP!")
    except Exception as e:
        print(f"Error sending email: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if not GMAIL_USER or not GMAIL_PASS:
        print("Error: GMAIL_USER or GMAIL_APP_PASSWORD secret missing.")
        sys.exit(1)
        
    data = fetch_top_real_estate()
    send_daily_email(data)
