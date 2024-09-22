from pymongo import MongoClient
from dotenv import load_dotenv
from threading import Thread
import os, json
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (MarketOrderRequest, LimitOrderRequest, ClosePositionRequest, 
                                     StopLimitOrderRequest, GetOrdersRequest)
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce, QueryOrderStatus


# Load environment variables
load_dotenv()

with open('config.json', 'r') as file:
    data = json.load(file)

# MongoDB Atlas setup
mongo_uri = os.getenv('MONGO_URI')
client = MongoClient(mongo_uri)
db = client['user_db']
webhooks_collection = db['webhook_data']

# Initialize the Alpaca Trading Client
api_key = data['APCA-API-KEY-ID']
api_secret = data['APCA-API-SECRET-KEY']


# Global variables to manage state
latest_processed_signal = None
processed_signals = set()


trade_client = TradingClient(api_key, api_secret, paper=True, url_override=None)

def calculate_stoploss(limit_price, qty):    
    total_value = limit_price * qty
    stoploss_value = total_value * 0.10
    stoploss_price = limit_price - stoploss_value
    
    return stoploss_price

def cancel_all_open_orders():
    try:
        # Attempt to cancel all open orders
        response = trade_client.cancel_orders()
        print("All open orders have been canceled successfully.")
        return response
    except Exception as e:
        print(f"An error occurred while canceling orders: {e}")
        return None
    
def get_all_positions():
    try:
        positions = trade_client.get_all_positions()
        return positions
    except Exception as e:
        print(f"An error occurred while retrieving positions: {e}")
        return None
    
def get_open_position(symbol):
    try:
        position = trade_client.get_open_position(symbol_or_asset_id=symbol)
        return position
    except Exception as e:
        print(f"An error occurred while retrieving the open position for {symbol}: {e}")
        return None

def get_time_in_force(time_in_force):
    if time_in_force == "day":
        return TimeInForce.DAY
    elif time_in_force == "gtc":
        return TimeInForce.GTC
    elif time_in_force == "opg":
        return TimeInForce.OPG
    elif time_in_force == "cls":
        return TimeInForce.CLS
    elif time_in_force == "ioc":
        return TimeInForce.IOC
    elif time_in_force == "fok":
        return TimeInForce.FOK
    else:
        raise ValueError("Invalid time in force specified.")


def create_order_request(order_type, symbol, qty, order_side, time_in_force, limit_price=None):
    if order_type == "market_order":
        return MarketOrderRequest(symbol=symbol, qty=qty, side=order_side, type=OrderType.MARKET, time_in_force=time_in_force)
    elif order_type == "limit_order":
        if limit_price is None:
            raise ValueError("limitPrice is required for limit orders.")
        return LimitOrderRequest(symbol=symbol, qty=qty, limit_price=limit_price, side=order_side, type=OrderType.LIMIT, time_in_force=time_in_force)
    elif order_type == "stop_loss_limit_order":
        if limit_price is None:
            raise ValueError("Both limitPrice and stoploss are required for stop limit orders.")
        stoploss = calculate_stoploss(limit_price, qty)
        return StopLimitOrderRequest(symbol=symbol, qty=qty, side=order_side, time_in_force=time_in_force, limit_price=limit_price, stop_price=stoploss)
    else:
        raise ValueError("Invalid order type specified.")


def close_position(symbol):
    try:
        open_orders = get_open_position(symbol)

        trade_client.close_position(
            symbol_or_asset_id=symbol,
            close_options=ClosePositionRequest(qty=str(abs(int(open_orders.qty))))  
        )
        print(f"Position closed for {symbol}.")
    except Exception as e:
        print(f"An unexpected error occurred while closing position: {e}")



def determine_position_size(symbol, order_price):
    try:
        if order_price <= 0:
            raise ValueError("Order price must be greater than zero.")

        account = trade_client.get_account()
        cash_balance = float(account.portfolio_value)
        max_trade_amount = cash_balance * 0.20
        max_quantity_by_value = max_trade_amount // order_price
        position_size = min(max_quantity_by_value, 100)
        return int(position_size)
    except Exception as e:
        print(f"An error occurred while determining position size for {symbol}: {e}")
        return None


