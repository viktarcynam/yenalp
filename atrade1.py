import requests
import json
import threading
import time
import datetime
import sys
import select
import termios
import tty
import msgpack

# Alpaca API client
class AlpacaClient:
    def __init__(self, api_key, secret_key, paper=True):
        self.api_key = api_key
        self.secret_key = secret_key
        # Note: v2 is the general version for trading, accounts, etc.
        self.base_url = "https://paper-api.alpaca.markets/v2" if paper else "https://api.alpaca.markets/v2"
        # Market data has a different URL structure
        self.data_url = "https://data.alpaca.markets/v2" # For stocks
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

import websocket

# Websocket client for real-time option data
class WebsocketClient(threading.Thread):
    def __init__(self, api_key, secret_key, paper=True):
        super().__init__()
        self.daemon = True  # Allows main thread to exit even if this thread is running
        self.api_key = api_key
        self.secret_key = secret_key
        self.ws_url = "wss://stream.data.sandbox.alpaca.markets/v1beta1/options" if paper else "wss://stream.data.alpaca.markets/v1beta1/options"
        self.ws = None
        self.quotes = {}
        self.lock = threading.Lock()
        self._subscriptions = set()

    def run(self):
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open
        )
        self.ws.run_forever()

    def _on_open(self, ws):
        print("Websocket opened.")
        auth_message = {
            "action": "auth",
            "key": self.api_key,
            "secret": self.secret_key
        }
        ws.send(json.dumps(auth_message))

    def _on_message(self, ws, message):
        try:
            unpacked_message = msgpack.unpackb(message)

            for item in unpacked_message:
                msg_type = item.get("T")
                if msg_type == "success" and item.get("msg") == "authenticated":
                    print("Websocket authenticated.")
                    # Resubscribe to any symbols that were requested before connection
                    self.subscribe(list(self._subscriptions))
                elif msg_type == "subscription":
                     print(f"Subscription confirmation: {item}")
                elif msg_type == "q":  # It's a quote
                    symbol = item.get("S")
                    with self.lock:
                        self.quotes[symbol] = {
                            "bid": item.get("bp"),
                            "ask": item.get("ap"),
                            "timestamp": item.get("t")
                        }
                elif msg_type == "error":
                    print(f"Websocket error message: {item}")

        except Exception as e:
            print(f"Error processing websocket message: {e}")


    def _on_error(self, ws, error):
        print(f"Websocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        print("Websocket closed.")

    def subscribe(self, symbols):
        if not isinstance(symbols, list):
            symbols = [symbols]

        new_symbols = [s for s in symbols if s not in self._subscriptions]
        if not new_symbols:
            return

        self._subscriptions.update(new_symbols)

        if self.ws and self.ws.sock and self.ws.sock.connected:
            subscription_message = {
                "action": "subscribe",
                "quotes": new_symbols
            }
            self.ws.send(json.dumps(subscription_message))
            print(f"Subscribed to quotes for: {new_symbols}")

    def get_quote(self, symbol):
        with self.lock:
            return self.quotes.get(symbol)

# Helper functions
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


def place_and_monitor_order(client, occ_symbol, quantity, action, price):
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

    poll_order_status(client, order_id)


def atrade1_main():
    """Main function for the interactive Alpaca option client."""
    print("atrade1 : Alpaca Interactive Option Client")

    # In a real application, you'd get these from a secure source
    api_key = input("Enter your Alpaca API Key ID: ")
    secret_key = input("Enter your Alpaca Secret Key: ")

    print("\nStarting websocket client...")
    websocket_client = WebsocketClient(api_key, secret_key)
    websocket_client.start()
    time.sleep(2) # Give the websocket time to connect and authenticate

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

            # 4. Get option contracts (simplified flow)
            expiry_date_str = input("Enter option expiry date (YYYY-MM-DD): ")
            contracts_response = client.get_option_contracts(symbol_input, expiration_date=expiry_date_str)

            if not contracts_response.get("success"):
                print(f"Error getting option contracts: {contracts_response.get('error')}")
                continue

            contracts = contracts_response.get("data", {}).get("option_contracts", [])
            if not contracts:
                print("No option contracts found for the specified symbol and expiry.")
                continue

            # 5. Subscribe to quotes and display them
            contract_symbols = [c.get("symbol") for c in contracts]
            websocket_client.subscribe(contract_symbols)

            print("\nFetching real-time quotes...")
            time.sleep(1.5) # Wait for quotes to come in

            print("\n--- Option Chain (First 10 Contracts) ---")
            for contract in contracts[:10]:
                occ_symbol = contract.get("symbol")
                quote = websocket_client.get_quote(occ_symbol)

                type = contract.get('type')
                strike = contract.get('strike_price')

                if quote:
                    print(f"  {type.upper()} {strike} | Bid: {quote.get('bid'):.2f}, Ask: {quote.get('ask'):.2f}  ({occ_symbol})")
                else:
                    print(f"  {type.upper()} {strike} | No quote available.  ({occ_symbol})")


            # 6. Prompt for action
            action_input = input("ACTION - (B/S C/P PRICE [QTY], e.g., B C 1.25 5): ").upper().strip()
            parts = action_input.split()
            if len(parts) < 3 or len(parts) > 4:
                print("Invalid action format. Use: B/S C/P PRICE [QTY]")
                continue

            action, option_type_in, price_str = parts[0], parts[1], parts[2]
            quantity = 1
            if len(parts) == 4:
                try:
                    quantity = int(parts[3])
                except ValueError:
                    print("Invalid quantity. Must be an integer.")
                    continue

            if action not in ['B', 'S'] or option_type_in not in ['C', 'P']:
                print("Invalid action or option type.")
                continue

            try:
                price = float(price_str)
            except ValueError:
                print("Invalid price.")
                continue

            # Find the specific contract the user wants to trade
            # For simplicity, we'll find the closest strike to the last price
            # A more robust implementation would let the user select from the list
            last_price = client.get_stock_quote(symbol_input)["data"].get("quote", {}).get("ap")

            def get_nearest_strike(price):
                return round(price / 5) * 5 # Simple logic, can be improved

            target_strike = get_nearest_strike(last_price)

            target_contract = None
            for c in contracts:
                if c.get("type") == option_type_in.lower() and abs(float(c.get("strike_price")) - target_strike) < 0.1:
                    target_contract = c
                    break

            if not target_contract:
                print(f"Could not find a {option_type_in} contract near strike {target_strike}.")
                continue

            occ_symbol = target_contract["symbol"]
            print(f"Selected contract: {occ_symbol}")

            # 7. Validate price against live quote
            live_quote = websocket_client.get_quote(occ_symbol)
            if not live_quote:
                print("Could not get a live quote for this contract. Cannot validate price.")
                # Allow trade anyway? For now, we will stop.
                continue

            market_bid = live_quote.get("bid")
            market_ask = live_quote.get("ask")

            if action == 'B' and price > market_ask:
                print(f"Invalid price for buy order. Price ({price:.2f}) cannot be higher than ask ({market_ask:.2f}).")
                continue

            if action == 'S' and price < market_bid:
                print(f"Invalid price for sell order. Price ({price:.2f}) cannot be lower than bid ({market_bid:.2f}).")
                continue

            print("Price is valid.")
            place_and_monitor_order(client, occ_symbol, quantity, action, price)


        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"\nAn unexpected error occurred: {e}")
            continue

if __name__ == "__main__":
    atrade1_main()
