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

    def get_latest_stock_trade(self, symbol):
        # This uses the data URL for market data
        return self.get(f"/stocks/{symbol}/trades/latest", base_url_override=self.data_url)

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

    def get_open_orders(self, symbols=None):
        params = {
            "status": "open",
            "symbols": symbols if symbols else None
        }
        params = {k: v for k, v in params.items() if v is not None}
        return self.get("/orders", params=params)

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

def find_and_adopt_orphaned_order(client, symbol_input):
    """Checks for existing open orders for a symbol and offers to adopt them."""
    print("\nChecking for existing working orders...")
    # We can't filter by underlying symbol directly, so we get all open orders
    # and filter them locally. This is a limitation of the API.
    # A better approach would be to get all option symbols for the underlying first,
    # then pass that list to the `symbols` parameter. For now, this is simpler.
    open_orders_response = client.get_open_orders()
    if not open_orders_response.get("success"):
        print("Could not retrieve open orders.")
        return False # Indicate that we are not adopting an order

    working_orders = [
        order for order in open_orders_response.get("data", [])
        if order.get("symbol", "").startswith(symbol_input.upper())
    ]

    if not working_orders:
        print("No working orders found for this symbol.")
        return False

    print("\n--- Found Orphaned Order(s)! ---")
    for i, order in enumerate(working_orders):
        print(f"  {i+1}. {order['symbol']} | {order['side']} {order['qty']} @ {order['limit_price']} | Status: {order['status']}")

    orphaned_order = None
    if len(working_orders) == 1:
        adopt_choice = input("Do you want to adopt and monitor this order? (y/n): ").lower()
        if adopt_choice == 'y':
            orphaned_order = working_orders[0]
    else:
        try:
            choice = int(input("Select an order to adopt (or 0 to skip): "))
            if choice > 0:
                orphaned_order = working_orders[choice - 1]
        except (ValueError, IndexError):
            print("Invalid selection.")
            return False

    if not orphaned_order:
        return False # User chose not to adopt

    adopt = 'y'
    if adopt == 'y':
        position_intent = "close" if "close" in orphaned_order.get("position_intent", "") else "open"

        order_to_monitor = {
            "id": orphaned_order["id"],
            "symbol": orphaned_order["symbol"],
            "quantity": float(orphaned_order["qty"]),
            "side": orphaned_order["side"],
            "action": 'B' if orphaned_order["side"] == "buy" else 'S',
            "price": float(orphaned_order["limit_price"])
        }

        status = poll_order_status(client, order_to_monitor)

        if status == "FILLED" and position_intent == "open":
            # After adopting and filling an opening leg, go into the closing workflow
            place_and_monitor_order(client, order_to_monitor["symbol"], order_to_monitor["quantity"], 'S' if order_to_monitor['action'] == 'B' else 'B', 0, "close") # Price 0 is a placeholder

        return True # Indicate we handled an adopted order

    return False


# Main application logic
def poll_order_status(client, order_to_monitor):
    """Polls an order's status and allows for adjustment or cancellation."""
    order_id = order_to_monitor["id"]
    if "original_start_time" not in order_to_monitor:
        now = time.time()
        order_to_monitor["original_start_time"] = now
        order_to_monitor["last_interaction_time"] = now

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

                    current_order_response = client.get_order(order_id)
                    current_status = current_order_response.get("data", {}).get("status")
                    non_replaceable_statuses = ['accepted', 'pending_new', 'pending_cancel', 'pending_replace', 'filled', 'canceled', 'expired', 'rejected']

                    if current_status in non_replaceable_statuses:
                        # --- Cancel-and-Replace Workflow ---
                        print(f"\n--- Adjusting Order (Cancel-and-Replace) ---")
                        print(f"  Order: {order_to_monitor['action']} {order_to_monitor['quantity']} {order_to_monitor['symbol']} @ {order_to_monitor['price']:.2f}")

                        # Get and display live quote
                        parsed_symbol = parse_occ_symbol(order_to_monitor['symbol'])
                        chain_response = client.get_option_chain(parsed_symbol['underlying'])
                        snapshots = chain_response.get("data", {}).get("snapshots", {})
                        live_quote = snapshots.get(order_to_monitor['symbol'], {}).get("latestQuote", {})
                        print(f"  Live Quote: Bid: {live_quote.get('bp', 0):.2f} / Ask: {live_quote.get('ap', 0):.2f}")

                        new_price_str = input("Enter new limit price (or 'q' to cancel adjustment): ").lower()

                        if new_price_str == 'q':
                            print("Adjustment cancelled.")
                        else:
                            try:
                                new_price = float(new_price_str)
                                print("\nCanceling original order...")
                                cancel_response = client.cancel_order(order_id)
                                if not cancel_response.get("success"):
                                    print(f"Failed to cancel order: {cancel_response.get('error')}")
                                    continue

                                print("Waiting for cancellation confirmation...")
                                while True:
                                    check_status_res = client.get_order(order_id)
                                    if check_status_res.get("data", {}).get("status") == "canceled":
                                        print("Cancellation confirmed.")
                                        break
                                    time.sleep(1)

                                print("Placing new order...")
                                new_order_response = client.place_order(
                                    symbol=order_to_monitor["symbol"],
                                    qty=order_to_monitor["quantity"],
                                    side=order_to_monitor["side"],
                                    order_type="limit",
                                    time_in_force="day",
                                    limit_price=new_price
                                )

                                if new_order_response.get("success"):
                                    new_order_id = new_order_response["data"].get("id")
                                    print(f"New order placed successfully. New Order ID: {new_order_id}")
                                    order_id = new_order_id
                                    order_to_monitor["id"] = new_order_id
                                    order_to_monitor["price"] = new_price
                                    order_to_monitor["last_interaction_time"] = time.time()
                                else:
                                    print(f"Failed to place new order: {new_order_response.get('error')}")

                            except ValueError:
                                print("Invalid price.")
                    else:
                        # --- Standard Replace Workflow ---
                        print(f"\n--- Adjusting Order (Standard Replace) ---")
                        print(f"  Order: {order_to_monitor['action']} {order_to_monitor['quantity']} {order_to_monitor['symbol']} @ {order_to_monitor['price']:.2f}")

                        # Get and display live quote
                        parsed_symbol = parse_occ_symbol(order_to_monitor['symbol'])
                        chain_response = client.get_option_chain(parsed_symbol['underlying'])
                        snapshots = chain_response.get("data", {}).get("snapshots", {})
                        live_quote = snapshots.get(order_to_monitor['symbol'], {}).get("latestQuote", {})
                        print(f"  Live Quote: Bid: {live_quote.get('bp', 0):.2f} / Ask: {live_quote.get('ap', 0):.2f}")

                        new_price_str = input("Enter new limit price (or 'q' to cancel adjustment): ").lower()

                        if new_price_str == 'q':
                            print("Adjustment cancelled.")
                        else:
                            try:
                                new_price = float(new_price_str)
                                replace_response = client.replace_order(order_id, limit_price=new_price)
                                if replace_response.get("success"):
                                    new_order_data = replace_response.get("data", {})
                                    print("Order replaced successfully.")
                                    order_id = new_order_data.get("id", order_id)
                                    order_to_monitor["id"] = order_id
                                    order_to_monitor["price"] = new_price
                                    order_to_monitor["last_interaction_time"] = time.time()
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

            # Get live quote for periodic display
            parsed_symbol = parse_occ_symbol(order_to_monitor['symbol'])
            if parsed_symbol:
                underlying = parsed_symbol['underlying']
                strike = parsed_symbol['strike_price']

                chain_response = client.get_option_chain(underlying)
                snapshots = chain_response.get("data", {}).get("snapshots", {})

                call_symbol_to_find = create_occ_symbol(underlying, parsed_symbol['expiration_date'], 'C', strike)
                put_symbol_to_find = create_occ_symbol(underlying, parsed_symbol['expiration_date'], 'P', strike)

                call_quote = snapshots.get(call_symbol_to_find, {}).get("latestQuote", {})
                put_quote = snapshots.get(put_symbol_to_find, {}).get("latestQuote", {})

                order_type_str = parsed_symbol['type'].capitalize()

                # Calculate elapsed times
                interaction_seconds = int(time.time() - order_to_monitor["last_interaction_time"])
                total_seconds = int(time.time() - order_to_monitor["original_start_time"])

                def format_time(s):
                    mins, secs = divmod(s, 60)
                    return f"{mins}m{secs}s" if mins > 0 else f"{secs}s"

                interaction_str = format_time(interaction_seconds)
                total_str = format_time(total_seconds)

                display_line = (
                    f"\r{status.capitalize()} {interaction_str}:{total_str} : "
                    f"{underlying} {order_to_monitor['side'].capitalize()} {order_to_monitor['quantity']} "
                    f"{order_type_str} {strike:.2f} @{order_to_monitor['price']:.2f} | "
                    f"CALL: {call_quote.get('bp', 0):.2f} / {call_quote.get('ap', 0):.2f} | "
                    f"PUT: {put_quote.get('bp', 0):.2f} / {put_quote.get('ap', 0):.2f}"
                )
                print(display_line, end=" " * 15) # Padding to clear previous line
            else:
                print(f"\rStatus: {status.upper()}", end="")

            if status == "filled":
                print("\nOrder Filled!")
                return "FILLED"
            elif status in ["canceled", "expired", "rejected"]:
                print(f"\nOrder is no longer active. Status: {status}")
                return status.upper()

            time.sleep(3)

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

    now = time.time()
    order_to_monitor = {
        "id": order_id,
        "symbol": occ_symbol,
        "quantity": quantity,
        "side": side,
        "action": action, # 'B' or 'S'
        "price": price,
        "original_start_time": now,
        "last_interaction_time": now
    }

    status = poll_order_status(client, order_to_monitor)

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

        closing_order_to_monitor = {
            "id": closing_order_id,
            "symbol": occ_symbol,
            "quantity": quantity,
            "side": closing_side,
            "action": closing_action,
            "price": closing_price
        }
        poll_order_status(client, closing_order_to_monitor)


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

    is_paper = os.getenv("APCA_PAPER_TRADING", "true").lower() == "true"
    client = AlpacaClient(api_key, secret_key, paper=is_paper)

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

            # 2. Get last trade price
            trade_response = client.get_latest_stock_trade(symbol_input)
            if not trade_response.get("success") or not trade_response.get("data", {}).get("trade"):
                print(f"Error getting last trade for {symbol_input}: {trade_response.get('error', 'No trade data')}")
                continue

            last_price = trade_response["data"].get("trade", {}).get("p")
            if last_price is None:
                print(f"Could not determine last price for {symbol_input}.")
                continue
            print(f"Last price for {symbol_input}: {last_price}")

            # 3. Get positions and check for orphaned orders
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

            if find_and_adopt_orphaned_order(client, symbol_input):
                continue # If an order was adopted, restart the main loop

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

            # Default to the soonest expiration date
            default_expiry = expirations[0]
            expiry_input = input(f"\nEnter expiration date (default: {default_expiry}): ")

            if not expiry_input:
                selected_expiry = default_expiry
            else:
                selected_expiry = expiry_input

            if selected_expiry not in expirations:
                print("Invalid date. Please choose a date from the available list:")
                for exp_date in expirations:
                    print(f" - {exp_date}")
                continue

            # Filter contracts for the selected expiry
            contracts_for_expiry = {s: p for s, p in parsed_contracts.items() if p["expiration_date"] == selected_expiry}

            # Separate calls and puts
            calls = {p["strike_price"]: s for s, p in contracts_for_expiry.items() if p["type"] == "call"}
            puts = {p["strike_price"]: s for s, p in contracts_for_expiry.items() if p["type"] == "put"}

            sorted_strikes = sorted(calls.keys() | puts.keys())

            # 5. Prompt for Strike Price
            suggested_strike_index = min(range(len(sorted_strikes)), key=lambda i: abs(sorted_strikes[i] - last_price))
            suggested_strike = sorted_strikes[suggested_strike_index]

            strike_str = input(f"\nEnter strike price (default: {suggested_strike}): ")
            if not strike_str:
                strike_price = suggested_strike
            else:
                try:
                    strike_price = float(strike_str)
                except ValueError:
                    print("Invalid strike price.")
                    continue

            # Display the focused part of the chain
            print(f"\n--- Option Chain for {selected_expiry} ---")
            print("CALLS (BID / ASK)  |  STRIKE  |  PUTS (BID / ASK)")
            print("------------------- | -------- | -----------------")

            try:
                selected_strike_index = sorted_strikes.index(strike_price)
                start_index = max(0, selected_strike_index - 1)
                end_index = min(len(sorted_strikes), selected_strike_index + 2)
                strikes_to_display = sorted_strikes[start_index:end_index]
            except ValueError:
                print("Selected strike not found in the list.")
                continue

            for strike in strikes_to_display:
                call_symbol = calls.get(strike)
                put_symbol = puts.get(strike)

                call_quote = snapshots.get(call_symbol, {}).get("latestQuote", {}) if call_symbol else {}
                put_quote = snapshots.get(put_symbol, {}).get("latestQuote", {}) if put_symbol else {}

                call_str = f"{call_quote.get('bp', 0):.2f} / {call_quote.get('ap', 0):.2f}".center(19)
                put_str = f"{put_quote.get('bp', 0):.2f} / {put_quote.get('ap', 0):.2f}".center(17)

                strike_display = f"{strike:.2f}".center(8)
                print(f"{call_str} | {strike_display} | {put_str}")

            # 6. Prompt for action
            action_input = input("\nACTION - (B/S C/P PRICE [QTY], e.g., B C 1.25 5): ").upper().strip()
            parts = action_input.split()
            if len(parts) < 3 or len(parts) > 4:
                print("Invalid action format. Use: B/S C/P PRICE [QTY]")
                continue

            action, option_type_in, price_str = parts[0], parts[1], parts[2]
            quantity = 1
            if len(parts) == 4:
                quantity = int(parts[3])

            try:
                price = float(price_str)
            except ValueError:
                print("Invalid price format.")
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
