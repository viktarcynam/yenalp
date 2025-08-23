import requests
import json
import threading
import time
import datetime
import sys
import select
import termios
import tty
import os
from dotenv import load_dotenv

# Alpaca API client
class AlpacaClient:
    def __init__(self, api_key, secret_key, paper=True):
        self.api_key = api_key
        self.secret_key = secret_key
        # Note: v2 is the general version for trading, accounts, etc.
        self.base_url = "https://paper-api.alpaca.markets/v2" if paper else "https://api.alpaca.markets/v2"
        # Market data has a different URL structure
        self.data_url = "https://data.alpaca.markets/v2" # For stocks
        self.data_v1beta1_url = "https://data.alpaca.markets/v1beta1" # For options snapshots
        self._session = requests.Session()
        self._session.headers.update({
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json"
        })

    def _request(self, method, url, params=None, data=None):
        """Helper method for making authenticated requests."""
        try:
            response = self._session.request(method, url, params=params, json=data)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            if response.status_code == 204:  # No content, like a successful DELETE
                return {"success": True, "data": None}
            return {"success": True, "data": response.json()}
        except requests.exceptions.HTTPError as e:
            error_message = f"HTTP Error: {e.response.status_code}"
            try:
                # Try to get a more specific error from the response body
                error_details = e.response.json()
                error_message += f" - {error_details.get('message', e.response.text)}"
            except json.JSONDecodeError:
                error_message += f" - {e.response.text}"
            return {"success": False, "error": error_message}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Request Exception: {e}"}

    def get(self, path, params=None, base_url_override=None):
        url = f"{base_url_override or self.base_url}{path}"
        return self._request("GET", url, params=params)

    def post(self, path, data=None):
        url = f"{self.base_url}{path}"
        return self._request("POST", url, data=data)

    def patch(self, path, data=None):
        url = f"{self.base_url}{path}"
        return self._request("PATCH", url, data=data)

    def delete(self, path):
        url = f"{self.base_url}{path}"
        return self._request("DELETE", url)

    # --- Alpaca API Methods ---
    def get_account(self):
        return self.get("/account")

    def get_stock_quote(self, symbol):
        # This uses the data URL for market data
        return self.get(f"/stocks/{symbol}/quotes/latest", base_url_override=self.data_url)

    def get_positions(self):
        return self.get("/positions")

    def get_option_contracts(self, underlying_symbol, status=None, expiration_date=None, strike_price_gte=None, strike_price_lte=None):
        params = {
            "underlying_symbols": underlying_symbol,
            "status": status,
            "expiration_date": expiration_date,
            "strike_price_gte": strike_price_gte,
            "strike_price_lte": strike_price_lte,
            "limit": 500  # Get a decent number of contracts
        }
        params = {k: v for k, v in params.items() if v is not None}
        return self.get("/options/contracts", params=params)

    def place_order(self, symbol, qty, side, order_type, time_in_force, limit_price=None):
        data = {
            "symbol": symbol,
            "qty": str(qty),  # API expects string for qty
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price is not None:
            data["limit_price"] = str(limit_price)
        return self.post("/orders", data=data)

    def replace_order(self, order_id, qty=None, time_in_force=None, limit_price=None):
        data = {}
        if qty is not None:
            data["qty"] = str(qty)
        if time_in_force is not None:
            data["time_in_force"] = time_in_force
        if limit_price is not None:
            data["limit_price"] = str(limit_price)
        return self.patch(f"/orders/{order_id}", data=data)

    def cancel_order(self, order_id):
        return self.delete(f"/orders/{order_id}")

    def get_order(self, order_id):
        return self.get(f"/orders/{order_id}")

    def get_option_chain(self, underlying_symbol):
        return self.get(f"/options/snapshots/{underlying_symbol}", base_url_override=self.data_v1beta1_url)

# Helper functions
def parse_occ_symbol(symbol):
    """
    Parses an OCC-formatted option symbol.
    Example: HOG250829C00027000 -> {underlying: HOG, expiry: 2025-08-29, type: call, strike: 27.0}
    """
    # Find the split point between the underlying symbol and the date
    for i, char in enumerate(symbol):
        if char.isdigit():
            underlying = symbol[:i]
            rest = symbol[i:]
            break
    else:
        return None # No digits found

    try:
        # Extract date, type, and strike
        expiry_str = rest[:6]
        option_type = rest[6]
        strike_price_str = rest[7:]

        # Format parts
        expiry = datetime.datetime.strptime(expiry_str, "%y%m%d").strftime("%Y-%m-%d")
        option_type_full = "call" if option_type == 'C' else "put"
        strike_price = float(strike_price_str) / 1000.0

        return {
            "underlying": underlying,
            "expiration_date": expiry,
            "type": option_type_full,
            "strike_price": strike_price
        }
    except (ValueError, IndexError):
        return None


def create_occ_symbol(underlying, expiry_date, option_type, strike):
    """
    Creates an OCC-formatted option symbol.
    Example: AAPL240119C00100000
    """
    # Format the expiry date from YYYY-MM-DD to YYMMDD
    try:
        expiry = datetime.datetime.strptime(expiry_date, "%Y-%m-%d").strftime("%y%m%d")
    except ValueError:
        print("Error: Invalid date format. Please use YYYY-MM-DD.")
        return None

    # Format the strike price to an 8-digit string with 3 decimal places (e.g., 123.5 -> 00123500)
    try:
        strike_price_formatted = f"{int(float(strike) * 1000):08d}"
    except ValueError:
        print("Error: Invalid strike price. Must be a number.")
        return None

    # Get the option type
    opt_type = option_type.upper()
    if opt_type not in ['C', 'P']:
        print("Error: Invalid option type. Must be 'C' or 'P'.")
        return None

    # Combine the parts
    return f"{underlying.upper()}{expiry}{opt_type}{strike_price_formatted}"

# Main application logic
def poll_order_status(client, order_id):
    """Polls an order's status and allows for adjustment or cancellation."""
    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        print("\nMonitoring order status... Press 'A' to adjust, 'Q' to cancel.")

        while True:
            # Check for user input
            if select.select([sys.stdin], [], [], 0.1)[0]:
                char = sys.stdin.read(1)
                if char.upper() == 'Q':
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                    confirm = input("\nAre you sure you want to cancel this order? (y/n): ").lower()
                    if confirm == 'y':
                        cancel_response = client.cancel_order(order_id)
                        if cancel_response.get("success"):
                            print("Order cancelled successfully.")
                            return "CANCELED"
                        else:
                            print(f"Failed to cancel order: {cancel_response.get('error')}")
                    else:
                        print("Cancellation aborted.")
                    tty.setcbreak(sys.stdin.fileno()) # Go back to listening

                elif char.upper() == 'A':
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                    new_price_str = input("\nEnter new limit price: ")
                    try:
                        new_price = float(new_price_str)
                        replace_response = client.replace_order(order_id, limit_price=new_price)
                        if replace_response.get("success"):
                            print("Order replaced successfully.")
                            # The new order will have the same ID
                        else:
                            print(f"Failed to replace order: {replace_response.get('error')}")
                    except ValueError:
                        print("Invalid price.")
                    tty.setcbreak(sys.stdin.fileno()) # Go back to listening


            # Check order status
            order_response = client.get_order(order_id)
            if not order_response.get("success"):
                print(f"\nError getting order status: {order_response.get('error')}")
                time.sleep(2)
                continue

            status = order_response["data"].get("status")
            print(f"\rOrder Status: {status.upper()}", end="")

            if status == "filled":
                print("\nOrder Filled!")
                return "FILLED"
            elif status in ["canceled", "expired", "rejected"]:
                print(f"\nOrder is no longer active. Status: {status}")
                return status.upper()

            time.sleep(2)

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


def place_and_monitor_order(client, occ_symbol, quantity, action, price, position_intent):
    """Places an order and then monitors it."""
    side = "buy" if action == 'B' else "sell"

    print(f"\nPlacing order: {action} {quantity} {occ_symbol} @ {price:.2f}")

    order_response = client.place_order(
        symbol=occ_symbol,
        qty=quantity,
        side=side,
        order_type="limit",
        time_in_force="day",
        limit_price=price
    )

    if not order_response.get("success"):
        print(f"Failed to place order: {order_response.get('error')}")
        return

    order_id = order_response["data"].get("id")
    print(f"Order placed successfully. Order ID: {order_id}")

    status = poll_order_status(client, order_id)

    # --- Round-trip logic ---
    if status == "FILLED" and position_intent == "open":
        print("\n--- Place Closing Order ---")

        # We need the underlying symbol from the OCC symbol to get the chain
        # Simple parsing: find the first digit
        underlying_symbol = ""
        for char in occ_symbol:
            if char.isdigit():
                break
            underlying_symbol += char

        chain_response = client.get_option_chain(underlying_symbol)
        if not chain_response.get("success"):
            print("Could not get latest chain to suggest a closing price. Aborting.")
            return

        snapshots = chain_response.get("data", {})
        target_snapshot = snapshots.get(occ_symbol)
        if not target_snapshot or not target_snapshot.get("quote"):
             print("Could not get latest quote to suggest a closing price. Aborting.")
             return

        closing_quote = target_snapshot["quote"]
        print(f"Latest quote for {occ_symbol}: Bid: {closing_quote['bp']:.2f}, Ask: {closing_quote['ap']:.2f}")

        try:
            closing_price_str = input("Enter limit price for closing order (or 's' to skip): ")
            if closing_price_str.lower() == 's':
                print("Skipping closing order.")
                return
            closing_price = float(closing_price_str)
        except ValueError:
            print("Invalid price. Aborting closing order.")
            return

        closing_action = 'S' if action == 'B' else 'B'
        closing_side = "sell" if closing_action == 'S' else "buy"

        print(f"\nPlacing closing order: {closing_action} {quantity} {occ_symbol} @ {closing_price:.2f}")

        closing_order_response = client.place_order(
            symbol=occ_symbol,
            qty=quantity,
            side=closing_side,
            order_type="limit",
            time_in_force="day",
            limit_price=closing_price
        )

        if not closing_order_response.get("success"):
            print(f"Failed to place closing order: {closing_order_response.get('error')}")
            return

        closing_order_id = closing_order_response["data"].get("id")
        print(f"Closing order placed successfully. Order ID: {closing_order_id}")

        poll_order_status(client, closing_order_id)


def atrade1_main():
    """Main function for the interactive Alpaca option client."""
    print("atrade1 : Alpaca Interactive Option Client")

    load_dotenv()
    api_key = os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("APCA_API_SECRET_KEY")

    if not api_key or not secret_key:
        print("\nAPI keys not found in .env file.")
        api_key = input("Enter your Alpaca API Key ID: ")
        secret_key = input("Enter your Alpaca Secret Key: ")

    client = AlpacaClient(api_key, secret_key)

    # 1. Verify connection by getting account info
    account_info = client.get_account()
    if not account_info.get("success"):
        print(f"Error connecting to Alpaca: {account_info.get('error')}")
        return

    print(f"\nSuccessfully connected. Account Status: {account_info['data'].get('status')}")

    while True:
        try:
            symbol_input = input("\nEnter a stock symbol (or 'q' to quit): ").upper()
            if symbol_input in ['QUIT', 'Q']:
                break

            # 2. Get stock quote
            quote_response = client.get_stock_quote(symbol_input)
            if not quote_response.get("success"):
                print(f"Error getting quote: {quote_response.get('error')}")
                continue

            last_price = quote_response["data"].get("quote", {}).get("ap") # Ask price as last price
            print(f"Last price for {symbol_input}: {last_price}")

            # 3. Get positions
            positions_response = client.get_positions()
            if not positions_response.get("success"):
                print(f"Error getting positions: {positions_response.get('error')}")
            else:
                symbol_positions = [p for p in positions_response["data"] if p.get("symbol").startswith(symbol_input)]
                if symbol_positions:
                    print(f"\nPositions for {symbol_input}:")
                    for pos in symbol_positions:
                        print(f"  {pos.get('symbol')}: Qty: {pos.get('qty')}, Side: {pos.get('side')}")
                else:
                    print(f"No positions found for {symbol_input}.")

            # 4. Get option chain
            chain_response = client.get_option_chain(symbol_input)
            if not chain_response.get("success"):
                print(f"Error getting option chain: {chain_response.get('error')}")
                continue

            snapshots = chain_response.get("data", {}).get("snapshots", {})
            if not snapshots:
                print("No option chain found for this symbol.")
                continue

            # Parse all symbols to extract details
            parsed_contracts = {}
            for symbol, details in snapshots.items():
                parsed = parse_occ_symbol(symbol)
                if parsed:
                    parsed_contracts[symbol] = parsed

            # Extract unique expiration dates from parsed data
            expirations = sorted(list(set([p["expiration_date"] for p in parsed_contracts.values()])))
            if not expirations:
                print("Could not parse any valid expiration dates.")
                continue

            print("\nAvailable expiration dates:")
            for i, exp_date in enumerate(expirations):
                print(f"  {i+1}. {exp_date}")

            try:
                choice = int(input("Select an expiration date: ")) - 1
                selected_expiry = expirations[choice]
            except (ValueError, IndexError):
                print("Invalid selection.")
                continue

            # Filter contracts for the selected expiry
            contracts_for_expiry = {s: p for s, p in parsed_contracts.items() if p["expiration_date"] == selected_expiry}

            # Separate calls and puts
            calls = {p["strike_price"]: s for s, p in contracts_for_expiry.items() if p["type"] == "call"}
            puts = {p["strike_price"]: s for s, p in contracts_for_expiry.items() if p["type"] == "put"}

            sorted_strikes = sorted(calls.keys() | puts.keys())

            print(f"\n--- Option Chain for {selected_expiry} ---")
            print("STRIKE   |   CALL (BID/ASK)   |   PUT (BID/ASK)")
            print("-------- | -------------------- | ------------------")

            num_strikes_to_show = 5
            try:
                closest_strike_index = min(range(len(sorted_strikes)), key=lambda i: abs(sorted_strikes[i] - last_price))
                start_index = max(0, closest_strike_index - num_strikes_to_show)
                end_index = min(len(sorted_strikes), closest_strike_index + num_strikes_to_show + 1)
                strikes_to_display = sorted_strikes[start_index:end_index]
            except (ValueError, IndexError):
                strikes_to_display = sorted_strikes[:10]

            for strike in strikes_to_display:
                call_symbol = calls.get(strike)
                put_symbol = puts.get(strike)

                call_quote = snapshots.get(call_symbol, {}).get("latestQuote", {}) if call_symbol else {}
                put_quote = snapshots.get(put_symbol, {}).get("latestQuote", {}) if put_symbol else {}

                call_str = f"{call_quote.get('bp', 0):.2f}/{call_quote.get('ap', 0):.2f}".center(20)
                put_str = f"{put_quote.get('bp', 0):.2f}/{put_quote.get('ap', 0):.2f}".center(18)

                print(f"{strike:<8.2f} | {call_str} | {put_str}")

            # 5. Prompt for action
            action_input = input("\nACTION - (B/S C/P STRIKE PRICE [QTY], e.g., B C 550 1.25 5): ").upper().strip()
            parts = action_input.split()
            if len(parts) < 4 or len(parts) > 5:
                print("Invalid action format. Use: B/S C/P STRIKE PRICE [QTY]")
                continue

            action, option_type_in, strike_str, price_str = parts[0], parts[1], parts[2], parts[3]
            quantity = 1
            if len(parts) == 5:
                quantity = int(parts[4])

            try:
                strike_price = float(strike_str)
                price = float(price_str)
            except ValueError:
                print("Invalid number format for strike or price.")
                continue

            # 6. Validate price against quote from snapshot
            target_symbol = None
            if option_type_in == 'C':
                target_symbol = calls.get(strike_price)
            else:
                target_symbol = puts.get(strike_price)

            if not target_symbol:
                print("Invalid strike price selected.")
                continue

            live_quote = snapshots.get(target_symbol, {}).get("latestQuote", {})
            market_bid = live_quote.get("bp", 0)
            market_ask = live_quote.get("ap", 0)

            if market_ask == 0 and action == 'B':
                 print("No ask price available for this contract. Cannot place buy order.")
                 continue

            if action == 'B' and price > market_ask:
                print(f"Invalid price for buy order. Price ({price:.2f}) cannot be higher than ask ({market_ask:.2f}).")
                continue

            if action == 'S' and price < market_bid:
                print(f"Invalid price for sell order. Price ({price:.2f}) cannot be lower than bid ({market_bid:.2f}).")
                continue

            # 7. Determine Position Intent
            current_position_qty = 0
            all_positions = client.get_positions()
            if all_positions.get("success"):
                for pos in all_positions.get("data", []):
                    if pos.get("symbol") == target_symbol:
                        current_position_qty = float(pos.get("qty", 0))
                        break

            position_intent = "open"
            if action == 'B' and current_position_qty < 0:
                position_intent = "close"
            elif action == 'S' and current_position_qty > 0:
                position_intent = "close"

            print(f"Price is valid. Position intent: {position_intent}")
            place_and_monitor_order(client, target_symbol, quantity, action, price, position_intent)


        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"\nAn unexpected error occurred: {e}")
            continue

if __name__ == "__main__":
    atrade1_main()
