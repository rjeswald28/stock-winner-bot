import os
import time
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, time as dt_time
from dotenv import load_dotenv

last_summary_date = None

last_scan_time = "Never"

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ALPHA_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

WATCHLIST = [
    "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "META", "GOOGL", "AMZN",
    "NFLX", "PLTR", "COIN", "MARA", "RIVN", "SOFI", "HOOD", "SMCI",
    "AVGO", "MU", "INTC", "BA", "DIS", "UBER", "SHOP", "XYZ",
    "SPY", "QQQ"
]

SCAN_INTERVAL_SECONDS = 600

alerted_today = set()

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": message
    })

def send_daily_summary():
    global last_summary_date

    today = datetime.now().strftime("%Y-%m-%d")

    if last_summary_date == today:
        return

    current_hour = datetime.now().hour

    if current_hour < 16:
        return

    try:
        df = pd.read_csv("paper_trading_log.csv")
    except:
        return

    today_df = df[df["time"].str.contains(today)]

    if today_df.empty:
        return

    total_alerts = len(today_df)

    avg_30 = pd.to_numeric(today_df["gain_30_min"], errors="coerce").mean()
    avg_1h = pd.to_numeric(today_df["gain_1_hour"], errors="coerce").mean()

    best_trade = today_df.loc[
        pd.to_numeric(today_df["gain_30_min"], errors="coerce").idxmax()
    ]

    worst_trade = today_df.loc[
        pd.to_numeric(today_df["gain_30_min"], errors="coerce").idxmin()
    ]

    summary = (
        f"📊 DAILY BOT SUMMARY\n\n"
        f"Date: {today}\n"
        f"Total Alerts: {total_alerts}\n\n"
        f"Average 30m Gain: {avg_30:.2f}%\n"
        f"Average 1h Gain: {avg_1h:.2f}%\n\n"
        f"🏆 Best Alert:\n"
        f"{best_trade['ticker']} ({best_trade['gain_30_min']}%)\n\n"
        f"📉 Worst Alert:\n"
        f"{worst_trade['ticker']} ({worst_trade['gain_30_min']}%)"
    )

    send_telegram(summary)

    last_summary_date = today

def get_news_catalyst(ticker):
    if not ALPHA_KEY:
        return 0, "No news API key"

    url = "https://www.alphavantage.co/query"

    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ticker,
        "apikey": ALPHA_KEY,
        "limit": 3
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        articles = data.get("feed", [])

        if not articles:
            return 0, "No recent news found"

        top_article = articles[0]
        title = top_article.get("title", "Recent news")
        sentiment = top_article.get("overall_sentiment_score", 0)

        if sentiment >= 0.25:
            return 2, f"Positive news: {title}"
        elif sentiment <= -0.25:
            return -1, f"Negative news: {title}"
        else:
            return 1, f"News catalyst: {title}"

    except Exception as e:
        return 0, f"News error: {e}"


def analyze_stock(ticker):
    data = yf.download(ticker, period="5d", interval="5m", progress=False, auto_adjust=True)
    spy_data = yf.download("SPY", period="5d", interval="5m", progress=False, auto_adjust=True)

    if data.empty or len(data) < 30 or spy_data.empty:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    if isinstance(spy_data.columns, pd.MultiIndex):
        spy_data.columns = spy_data.columns.get_level_values(0)

    close = data["Close"]
    high = data["High"]
    volume = data["Volume"]
    open_price = data["Open"]

    current_price = close.iloc[-1].item()
    first_price = close.iloc[0].item()
    today_open = open_price.iloc[-78].item() if len(open_price) >= 78 else open_price.iloc[0].item()

    recent_high = high.tail(30).max().item()
    avg_volume = volume.tail(30).mean().item()
    current_volume = volume.iloc[-1].item()

    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
    price_change = ((current_price - first_price) / first_price) * 100
    gap_percent = ((today_open - first_price) / first_price) * 100

    spy_close = spy_data["Close"]
    spy_change = ((spy_close.iloc[-1].item() - spy_close.iloc[0].item()) / 
    spy_close.iloc[0].item()) * 100

    relative_strength = price_change - spy_change

    score = 0
    reasons = []

    if volume_ratio >= 2:
        score += 3
        reasons.append(f"Heavy unusual volume: {volume_ratio:.2f}x avg")
    elif volume_ratio >= 1.5:
        score += 2
        reasons.append(f"Unusual volume: {volume_ratio:.2f}x avg")

    if current_price >= recent_high * 0.995:
        score += 2
        reasons.append("Near/breaking recent high")

    if abs(price_change) >= 3:
        score += 2
        reasons.append(f"Strong move: {price_change:.2f}%")
    elif abs(price_change) >= 2:
        score += 1
        reasons.append(f"Decent move: {price_change:.2f}%")

    if relative_strength >= 1:
        score += 2
        reasons.append(f"Outperforming SPY by {relative_strength:.2f}%")

    if abs(gap_percent) >= 1.5:
        score += 1
        reasons.append(f"Gap move: {gap_percent:.2f}%")

    if current_price > close.tail(50).mean().item():
        score += 1
        reasons.append("Above short-term trend")

        news_score, news_reason = get_news_catalyst(ticker)
        score += news_score

        if news_score != 0:
            reasons.append(news_reason)

    risk = "LOW"
    if abs(price_change) >= 5 or volume_ratio >= 4:
        risk = "HIGH"
    elif abs(price_change) >= 3 or volume_ratio >= 2.5:
        risk = "MEDIUM"
    confidence = "SPECULATIVE"

    if score >= 10 and risk != "HIGH":
        confidence = "ELITE"
    elif score >= 8:
        confidence = "STRONG"

    return {
        "ticker": ticker,
        "price": round(current_price, 2),
        "score": score,
        "risk": risk,
        "reasons": reasons,
        "confidence": confidence,
        "price_change": round(price_change, 2),
        "gap_percent": round(gap_percent, 2),
        "relative_strength": round(relative_strength, 2),
        "volume_ratio": round(volume_ratio, 2),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }    

def log_alert(result):
    file_name = "paper_trading_log.csv"

    row = {
        "time": result["time"],
        "gain_30_min": "",
        "gain_1_hour": "",
        "ticker": result["ticker"],
        "alert_price": result["price"],
        "score": result["score"],
        "risk": result["risk"],
        "reasons": " | ".join(result["reasons"]),
        "price_30_min": "",
        "price_1_hour": "",
        "price_1_day": "",
        "price_1_week": ""
    }

    df = pd.DataFrame([row])

    try:
        old = pd.read_csv(file_name)
        df = pd.concat([old, df], ignore_index=True)
    except FileNotFoundError:
        pass

    df.to_csv(file_name, index=False)

def get_top_movers():
    try:
        tables = pd.read_html("https://finance.yahoo.com/markets/stocks/gainers/")

        if not tables:
            return []

        df = tables[0]

        symbol_col = [col for col in df.columns if "Symbol" in str(col)]

        if not symbol_col:
            return []

        tickers = df[symbol_col[0]].dropna().tolist()

        cleaned = []

        for ticker in tickers[:15]:
            if isinstance(ticker, str):
                cleaned.append(ticker.upper())

        return cleaned

    except Exception as e:
        print(f"Top movers error: {e}")
        return []

def is_market_scan_time():
    now = datetime.now()

    # Monday=0, Sunday=6
    if now.weekday() > 4:
        return False

    current_time = now.time()

    premarket_start = dt_time(7, 0)
    market_close = dt_time(16, 30)

    return premarket_start <= current_time <= market_close


def scan_market():
    print("Scanning market...")

    global last_scan_time
    last_scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
   
    dynamic_watchlist = WATCHLIST + get_top_movers()
    unique_watchlist = list(set(dynamic_watchlist))

    for ticker in unique_watchlist:
        result = analyze_stock(ticker)

        if not result:
            continue

        print(result)

        alert_key = f"{result['ticker']}-{datetime.now().strftime('%Y-%m-%d')}"

        if result["score"] >= 8 and result["risk"] != "HIGH" and alert_key not in alerted_today:
            alerted_today.add(alert_key)

            chart_link = f"https://finance.yahoo.com/quote/{result['ticker']}"

            message = (
                f"🚨 STOCK ALERT\n\n"
                f"Ticker: ${result['ticker']}\n"
                f"Price: ${result['price']}\n"
                f"Score: {result['score']}/12\n"
                f"Confidence: {result['confidence']}\n"
                f"Risk: {result['risk']}\n"
                f"Move: {result['price_change']}%\n"
                f"Gap: {result['gap_percent']}%\n"
                f"Relative Strength vs SPY: {result['relative_strength']}%\n"
                f"Volume: {result['volume_ratio']}x avg\n\n"
                f"Reasons:\n- " + "\n- ".join(result["reasons"]) +
                f"\n\nQuick Read: {result['confidence']} setup with {result['volume_ratio']}x volume and {result['relative_strength']}% strength vs SPY.\n"
                f"\n\nChart: {chart_link}\n"
                f"Mode: Watchlist only, no buying."
            )

            send_telegram(message)
            log_alert(result)

def update_paper_trades():
    file_name = "paper_trading_log.csv"

    try:
        df = pd.read_csv(file_name)
    except FileNotFoundError:
        return

    if df.empty:
        return

    for index, row in df.iterrows():
        ticker = row["ticker"]

        try:
            current_data = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)

            if current_data.empty:
                continue

            if isinstance(current_data.columns, pd.MultiIndex):
                current_data.columns = current_data.columns.get_level_values(0)

            current_price = current_data["Close"].iloc[-1].item()
         
            alert_price = float(row["alert_price"])
            gain_percent = ((current_price - alert_price) / alert_price) * 100

            if pd.isna(row["price_30_min"]):
                df.at[index, "price_30_min"] = round(current_price, 2)
                df.at[index, "gain_30_min"] = round(gain_percent, 2)
            elif pd.isna(row["price_1_hour"]):
                df.at[index, "price_1_hour"] = round(current_price, 2)
                df.at[index, "gain_1_hour"] = round(gain_percent, 2)          

        except Exception as e:
            print(f"Paper trade update error for {ticker}: {e}")

    df.to_csv(file_name, index=False)

def check_telegram_commands():
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        response = requests.get(url, timeout=10)
        data = response.json()

        for update in data.get("result", [])[-5:]:
            message = update.get("message", {})
            text = message.get("text", "")
            chat = message.get("chat", {})
            chat_id = str(chat.get("id"))

            if text == "/status" and chat_id == str(CHAT_ID):
                send_telegram(
                    f"✅ Bot is online\n"
                    f"Last scan: {last_scan_time}\n"
                    f"Watchlist size: {len(WATCHLIST)}\n"
                    f"Mode: Watchlist + paper trading"
                )

    except Exception as e:
        print(f"Command check error: {e}")

def send_leaderboard():
    try:
        file_name = "paper_trading_log.csv"

        df = pd.read_csv(file_name)

        if df.empty:
            return

        completed = df.dropna(subset=["gain_1_hour"])

        if completed.empty:
            return

        top = completed.sort_values(by="gain_1_hour", ascending=False).head(5)

        message = "🏆 TOP STOCK ALERTS TODAY\n\n"

        for _, row in top.iterrows():
            message += (
                f"{row['ticker']} | "
                f"1H Gain: {row['gain_1_hour']}% | "
                f"Score: {row['score']}/12\n"
            )

        send_telegram(message)

    except Exception as e:
        print(f"Leaderboard error: {e}")

def main():
    send_telegram("✅ Family Stock Bot started. Watchlist mode only.")

    while True:
        if is_market_scan_time():
            scan_market()
            update_paper_trades()
            send_daily_summary()
            send_leaderboard()
        else:
            print("Outside market scan time. Waiting...")

        check_telegram_commands()
        time.sleep(SCAN_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
