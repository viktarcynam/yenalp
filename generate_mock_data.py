import json
from atrade1 import AlpacaClient

def main():
    """
    This script helps generate mock data for testing atrade1.py without
    needing direct access to API credentials.

    Please fill in your API keys below, run the script, and provide the
    output.
    """
    # --- PLEASE FILL IN YOUR CREDENTIALS ---
    API_KEY = "YOUR_API_KEY"
    SECRET_KEY = "YOUR_SECRET_KEY"
    PAPER_TRADING = True # Set to False for live account
    # -----------------------------------------

    if API_KEY == "YOUR_API_KEY":
        print("Please fill in your API_KEY and SECRET_KEY in the script.")
        return

    client = AlpacaClient(API_KEY, SECRET_KEY, paper=PAPER_TRADING)

    print("--- 1. Account Data ---")
    account_data = client.get_account()
    print(json.dumps(account_data, indent=2))

    print("\n--- 2. Positions Data ---")
    positions_data = client.get_positions()
    print(json.dumps(positions_data, indent=2))

    print("\n--- 3. Stock Quote (AAPL) ---")
    quote_data = client.get_stock_quote("AAPL")
    print(json.dumps(quote_data, indent=2))

    print("\n--- 4. Option Contracts (SPY, next month) ---")
    from datetime import datetime, timedelta
    today = datetime.now()
    next_month = today + timedelta(days=30)
    expiry_str = next_month.strftime("%Y-%m-%d")
    contracts_data = client.get_option_contracts("SPY", expiration_date=expiry_str)
    print(json.dumps(contracts_data, indent=2))

    print("\n--- 5. Order Details (if you have a recent order) ---")
    order_id_to_test = input("Enter an order ID to test (or press Enter to skip): ")
    if order_id_to_test:
        order_data = client.get_order(order_id_to_test)
        print(json.dumps(order_data, indent=2))
    else:
        print("Skipped.")

if __name__ == "__main__":
    main()
