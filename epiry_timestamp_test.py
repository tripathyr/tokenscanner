import arrow

# Sample expiry timestamp (UNIX time, e.g., from your error: 1696759529)
expiry_timestamp = 1696759529

try:
    # Convert Unix timestamp to Arrow datetime
    expiry_date = arrow.get(expiry_timestamp).to('utc')
    print(f"✅ Parsed expiry date: {expiry_date.format('YYYY-MM-DD HH:mm:ss ZZ')}")

    # Simulate check: has 30 days passed since expiry?
    now = arrow.utcnow()
    if now > expiry_date.shift(days=+30):
        print("🔔 30 days have passed since expiry. Refund should proceed.")
    else:
        print("⏳ Not yet 30 days since expiry. No refund yet.")

except Exception as e:
    print(f"❌ Error: {e}")
