import requests
import sys

# === CONFIGURATION ===
# Replace these with your actual values or run:
# python set_webhook.py YOUR_TELE_TOKEN YOUR_SPACE_URL

TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
SPACE_URL = "https://your-username-space-name.hf.space" 

if len(sys.argv) > 2:
    TELEGRAM_TOKEN = sys.argv[1]
    SPACE_URL = sys.argv[2]

WEBHOOK_URL = f"{SPACE_URL}/telegram"

print(f"🔧 Setting webhook...")
print(f"📍 Target: {WEBHOOK_URL}")

url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
try:
    response = requests.post(url, json={"url": WEBHOOK_URL})
    print(f"📨 Response: {response.status_code}")
    print(response.json())
    
    if response.status_code == 200 and response.json().get("ok"):
        print("\n✅ SUCCESS! Webhook is set.")
        print("Telegram will now send messages to your HF Space.")
    else:
        print("\n❌ FAILED. Check your token and URL.")

except Exception as e:
    print(f"\n❌ Error: {e}")
