import json
import os
from dotenv import load_dotenv
from atrade1 import AlpacaClient
from datetime import datetime, timedelta

def get_next_friday():
    """Returns the date of the next Friday in YYYY-MM-DD format."""
    today = datetime.now()
    # weekday() returns Monday as 0 and Sunday as 6. Friday is 4.
    days_until_friday = (4 - today.weekday() + 7) % 7
    if days_until_friday == 0: # If today is Friday, get next Friday
        days_until_friday = 7
    next_friday = today + timedelta(days=days_until_friday)
    return next_friday.strftime("%Y-%m-%d")

def main():
    """
    This script helps generate mock data for testing atrade1.py.
    It loads credentials from a .env file.
    """
    load_dotenv()

    API_KEY = os.getenv("APCA_API_KEY_ID")
    SECRET_KEY = os.getenv("APCA_API_SECRET_KEY")
    PAPER_TRADING = os.getenv("APCA_PAPER_TRADING", "true").lower() == "true"

    if not API_KEY or not SECRET_KEY:
        print("Please make sure your API keys are set in a .env file.")
        print("Create a .env file with APCA_API_KEY_ID='your_key' and APCA_API_SECRET_KEY='your_secret'")
        return

    client = AlpacaClient(API_KEY, SECRET_KEY, paper=PAPER_TRADING)

    print("--- 1. Account Data ---")
    account_data = client.get_account()
    print(json.dumps(account_data, indent=2))

    print("\n--- 2. Positions Data ---")
    positions_data = client.get_positions()
    print(json.dumps(positions_data, indent=2))

    print("\n--- 3. Stock Quote (HOG) ---")
    quote_data = client.get_stock_quote("HOG")
    print(json.dumps(quote_data, indent=2))

    print("\n--- 4. Option Chain Snapshot (HOG) ---")
    # This is the important part to debug the main application
    chain_data = client.get_option_chain("HOG")
    print(json.dumps(chain_data, indent=2))

    print("\n--- 5. Order Details (if you have a recent order) ---")
    order_id_to_test = input("Enter an order ID to test (or press Enter to skip): ")
    if order_id_to_test:
        order_data = client.get_order(order_id_to_test)
        print(json.dumps(order_data, indent=2))
    else:
        print("Skipped.")

    print("\n--- 6. Option Contracts (HOG, next Friday) ---")
    # This is to test the date logic
    expiry_str = get_next_friday()
    print(f"Testing with calculated next Friday: {expiry_str}")
    contracts_data = client.get_option_contracts("HOG", expiration_date=expiry_str)
    print(json.dumps(contracts_data, indent=2))


if __name__ == "__main__":
    main()
