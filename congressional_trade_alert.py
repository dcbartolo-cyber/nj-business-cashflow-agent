import os
import json
import re
import sys
import time
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
import yfinance as yf
from google import genai
from google.genai import types

# --- CONFIGURATION ---
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", GMAIL_USER)

# Target Politicians & Disclosed Name Variations
TARGET_POLITICIANS = {
    "Nancy Pelosi": ["Nancy Pelosi", "Pelosi, Nancy"],
    "Ro Khanna": ["Ro Khanna", "Khanna, Ro"],
    "Michael McCaul": ["Michael T. McCaul", "Michael McCaul", "McCaul, Michael T."]
}

STATE_FILE = "processed_trades.json"

def load_processed_trades():
    """Loads previously alerted trade IDs to prevent duplicate emails."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"Warning loading state file: {e}")
    return set()

def save_processed_trades(processed_ids):
    """Saves updated set of alerted trade IDs."""
    with open(STATE_FILE, "w") as f:
        json.dump(list(processed_ids), f, indent=2)

def fetch_recent_congressional_trades():
    """Fetches public House disclosures via House Stock Watcher S3 feed."""
    url = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
    print("Fetching latest House disclosure records...")
    try:
        res = requests.get(url, timeout=15)
        res.raise_for_status()
        all_trades = res.json()
    except Exception as e:
        print(f"Error fetching congressional disclosures: {e}")
        return []

    matched_trades = []
    
    for trade in all_trades:
        representative = trade.get("representative", "")
        ticker = trade.get("ticker", "").upper().strip()
        
        # Filter for valid stock tickers and targeted representatives
        if not ticker or ticker == "--" or "$" in ticker or len(ticker) > 5:
            continue
            
        for target_name, aliases in TARGET_POLITICIANS.items():
            if any(alias.lower() in representative.lower() for alias in aliases):
                # Unique transaction ID hash
                trade_id = f"{target_name}_{ticker}_{trade.get('transaction_date')}_{trade.get('type')}_{trade.get('amount')}"
                trade["normalized_name"] = target_name
                trade["trade_id"] = trade_id
                matched_trades.append(trade)
                break
                
    return matched_trades

def calculate_performance_metrics(ticker_symbol):
    """Calculates MoM, YTD, 3Y, and 5Y performance vs S&P 500 (^GSPC) and Nasdaq (^IXIC)."""
    print(f"Fetching market metrics and benchmark returns for {ticker_symbol}...")
    tickers_to_fetch = [ticker_symbol, "^GSPC", "^IXIC"]
    
    try:
        data = yf.download(tickers_to_fetch, period="5y", interval="1d", progress=False)["Close"]
        
        if ticker_symbol not in data or data[ticker_symbol].dropna().empty:
            return None

        stock = data[ticker_symbol].dropna()
        sp500 = data["^GSPC"].dropna()
        nasdaq = data["^IXIC"].dropna()

        curr_price = stock.iloc[-1]
        
        def get_return(series, days):
            if len(series) <= days:
                start_val = series.iloc[0]
            else:
                start_val = series.iloc[-days]
            return ((series.iloc[-1] - start_val) / start_val) * 100

        def get_ytd_return(series):
            curr_year = datetime.now().year
            ytd_series = series[series.index.year == curr_year]
            if ytd_series.empty:
                return 0.0
            start_val = ytd_series.iloc[0]
            return ((series.iloc[-1] - start_val) / start_val) * 100

        # Performance Calculations
        metrics = {
            "current_price": round(curr_price, 2),
            "mom_stock": round(get_return(stock, 21), 2),
            "mom_sp500": round(get_return(sp500, 21), 2),
            "mom_nasdaq": round(get_return(nasdaq, 21), 2),
            
            "ytd_stock": round(get_ytd_return(stock), 2),
            "ytd_sp500": round(get_ytd_return(sp500), 2),
            "ytd_nasdaq": round(get_ytd_return(nasdaq), 2),
            
            "3y_stock": round(get_return(stock, 252 * 3), 2),
            "3y_sp500": round(get_return(sp500, 252 * 3), 2),
            "3y_nasdaq": round(get_return(nasdaq, 252 * 3), 2),
            
            "5y_stock": round(get_return(stock, 252 * 5), 2),
            "5y_sp500": round(get_return(sp500, 252 * 5), 2),
            "5y_nasdaq": round(get_return(nasdaq, 252 * 5), 2),
        }

        # Fetch Ratings & Valuation Metrics via Ticker Info
        tk = yf.Ticker(ticker_symbol)
        info = tk.info or {}
        
        metrics["recommendation"] = info.get("recommendationKey", "N/A").replace("_", " ").title()
        metrics["target_price"] = info.get("targetMeanPrice", "N/A")
        metrics["forward_pe"] = info.get("forwardPE", "N/A")
        metrics["peg_ratio"] = info.get("pegRatio", "N/A")
        metrics["market_cap_b"] = round(info.get("marketCap", 0) / 1e9, 2) if info.get("marketCap") else "N/A"
        metrics["profit_margins"] = f"{round(info.get('profitMargins', 0) * 100, 2)}%" if info.get("profitMargins") else "N/A"

        return metrics
    except Exception as e:
        print(f"Error computing performance metrics for {ticker_symbol}: {e}")
        return None

def generate_ai_perspective(trade_info, metrics):
    """Uses Gemini 3.8 Flash to synthesize legislative context, federal contracts, and buy/avoid rationale."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    You are an institutional research analyst specializing in congressional trade tracking, corporate fundamentals, and policy-driven tailwinds.
    
    CONGRESSIONAL TRADE DETAILS:
    - Politician: {trade_info['normalized_name']}
    - Ticker: {trade_info['ticker']}
    - Transaction Type: {trade_info.get('type', 'Unknown')}
    - Estimated Transaction Amount: {trade_info.get('amount', 'Unknown')}
    - Transaction Date: {trade_info.get('transaction_date', 'Unknown')}
    - Disclosure Date: {trade_info.get('disclosure_date', 'Unknown')}
    
    FINANCIAL & VALUATION METRICS:
    - Current Price: ${metrics['current_price']}
    - Wall Street Consensus: {metrics['recommendation']} (Mean Target: ${metrics['target_price']})
    - Valuation: Forward P/E: {metrics['forward_pe']} | PEG Ratio: {metrics['peg_ratio']} | Market Cap: ${metrics['market_cap_b']}B
    - Performance (MoM): Stock {metrics['mom_stock']}% vs S&P 500 {metrics['mom_sp500']}% / Nasdaq {metrics['mom_nasdaq']}%
    - Performance (YTD): Stock {metrics['ytd_stock']}% vs S&P 500 {metrics['ytd_sp500']}% / Nasdaq {metrics['ytd_nasdaq']}%
    - Performance (3-Year): Stock {metrics['3y_stock']}% vs S&P 500 {metrics['3y_sp500']}% / Nasdaq {metrics['3y_nasdaq']}%
    - Performance (5-Year): Stock {metrics['5y_stock']}% vs S&P 500 {metrics['5y_sp500']}% / Nasdaq {metrics['5y_nasdaq']}%
    
    TASK:
    Provide a professional investment analysis covering:
    1. **Political & Legislative Catalysts**: Analyze the politician's committee assignments (e.g., McCaul on Foreign Affairs/Defense, Khanna on Armed Services/Cyber, Pelosi on Technology/Subsidies), federal contract exposure, and recent policy tailwinds relevant to this ticker.
    2. **Valuation & Growth Perspective**: Evaluate whether the stock is fairly valued, overextended, or priced for growth using the PEG, Forward P/E, and benchmark relative performance.
    3. **Additional Critical Metrics**: Identify 2-3 specific metrics the investor must monitor (e.g., Free Cash Flow yield, revenue backlog, institutional accumulation, debt-to-equity).
    4. **Final Stance & Rationale**: Provide a explicit recommendation ("BUY NOW", "WAIT FOR PULLBACK", or "AVOID") with clear reasoning on whether to follow this congressional trade.
    
    OUTPUT FORMAT:
    Return valid, clean HTML using <h4>, <p>, <ul>, <li>, and <strong> tags only. Do NOT wrap in markdown code blocks.
    """
    
    config = types.GenerateContentConfig(temperature=0.3)
    
    try:
        response = client.models.generate_content(
            model='gemini-3.8-flash',
            contents=prompt,
            config=config
        )
        return response.text.replace("```html", "").replace("```", "").strip()
    except Exception as e:
        print(f"Error generating AI perspective: {e}")
        return "<p>AI perspective generation temporarily unavailable.</p>"

def build_email_digest(trades_with_analysis):
    """Builds an executive HTML email body for new congressional trades."""
    html = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #222; line-height: 1.5; max-width: 750px; margin: auto;">
        <h2 style="color: #1a365d; border-bottom: 2px solid #2b6cb0; padding-bottom: 8px;">
            🏛️ Congressional Trade Alert: New Public Disclosures
        </h2>
        <p>The following transaction disclosures were recently published by the House Ethics Office for tracked members of Congress.</p>
    """
    
    for item in trades_with_analysis:
        t = item["trade"]
        m = item["metrics"]
        analysis = item["analysis"]
        
        tx_type = t.get("type", "Transaction").capitalize()
        type_badge = "background: #c6f6d5; color: #22543d;" if "purchase" in tx_type.lower() else "background: #fed7d7; color: #742a2a;"
        
        html += f"""
        <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin-bottom: 30px; box-shadow: 0 4px 6px rgba(0,0,0,0.04);">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h3 style="margin: 0; color: #2d3748;">{t['normalized_name']} — <span style="color: #3182ce;">{t['ticker']}</span></h3>
                <span style="padding: 4px 12px; border-radius: 12px; font-weight: bold; font-size: 13px; {type_badge}">{tx_type}</span>
            </div>
            
            <p style="color: #4a5568; font-size: 14px; margin-top: 6px;">
                <b>Disclosed Amount:</b> {t.get('amount', 'N/A')} | <b>Trade Date:</b> {t.get('transaction_date', 'N/A')} | <b>Filing Date:</b> {t.get('disclosure_date', 'N/A')}
            </p>
            
            <!-- Metrics Table -->
            <table style="width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 13px; text-align: left;">
                <thead>
                    <tr style="background: #edf2f7; color: #2d3748;">
                        <th style="padding: 8px; border: 1px solid #cbd5e0;">Timeframe</th>
                        <th style="padding: 8px; border: 1px solid #cbd5e0;">{t['ticker']} Return</th>
                        <th style="padding: 8px; border: 1px solid #cbd5e0;">S&P 500 (^GSPC)</th>
                        <th style="padding: 8px; border: 1px solid #cbd5e0;">Nasdaq (^IXIC)</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;"><b>1-Month (MoM)</b></td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['mom_stock']}%</td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['mom_sp500']}%</td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['mom_nasdaq']}%</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;"><b>Year-to-Date (YTD)</b></td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['ytd_stock']}%</td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['ytd_sp500']}%</td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['ytd_nasdaq']}%</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;"><b>3-Year Return</b></td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['3y_stock']}%</td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['3y_sp500']}%</td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['3y_nasdaq']}%</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;"><b>5-Year Return</b></td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['5y_stock']}%</td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['5y_sp500']}%</td>
                        <td style="padding: 8px; border: 1px solid #e2e8f0;">{m['5y_nasdaq']}%</td>
                    </tr>
                </tbody>
            </table>
            
            <p style="font-size: 13px; background: #f7fafc; padding: 10px; border-radius: 5px; border-left: 3px solid #4299e1;">
                <b>Wall Street Consensus:</b> {m['recommendation']} (Target: ${m['target_price']}) | 
                <b>Forward P/E:</b> {m['forward_pe']} | <b>PEG Ratio:</b> {m['peg_ratio']} | <b>Market Cap:</b> ${m['market_cap_b']}B
            </p>
            
            <div style="margin-top: 15px; border-top: 1px solid #edf2f7; padding-top: 10px;">
                {analysis}
            </div>
        </div>
        """
        
    html += "</body></html>"
    return html

def send_email(html_content, new_trades_count):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚨 Congressional Trade Alert: {new_trades_count} New Filing(s) Disclosed"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Reply-To"] = RECIPIENT_EMAIL
    
    plain_text = f"{new_trades_count} new congressional trades have been disclosed. Please view in an HTML-compatible email client."
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_content, "html"))
    
    print(f"Connecting to SMTP server to deliver alert to {RECIPIENT_EMAIL}...")
    server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
    server.login(GMAIL_USER, GMAIL_PASS)
    server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
    server.quit()
    print("SUCCESS: Congressional trade alert dispatched successfully!")

if __name__ == "__main__":
    if not GMAIL_USER or not GMAIL_PASS or not GEMINI_API_KEY:
        print("Error: Missing required environment secrets.")
        sys.exit(1)
        
    processed_ids = load_processed_trades()
    recent_trades = fetch_recent_congressional_trades()
    
    new_disclosures = []
    
    for trade in recent_trades:
        trade_id = trade["trade_id"]
        if trade_id not in processed_ids:
            ticker = trade["ticker"]
            metrics = calculate_performance_metrics(ticker)
            
            if metrics:
                print(f"Generating Gemini analysis for {trade['normalized_name']} trade on {ticker}...")
                analysis_html = generate_ai_perspective(trade, metrics)
                new_disclosures.append({
                    "trade": trade,
                    "metrics": metrics,
                    "analysis": analysis_html
                })
                processed_ids.add(trade_id)
            else:
                print(f"Skipping {ticker}: Could not retrieve financial metrics.")
                
    if new_disclosures:
        print(f"Found {len(new_disclosures)} new trade disclosure(s). Dispatching email...")
        email_html = build_email_digest(new_disclosures)
        send_email(email_html, len(new_disclosures))
        save_processed_trades(processed_ids)
    else:
        print("No new congressional trade disclosures found since last run.")
