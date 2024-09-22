from pymongo import MongoClient
from dotenv import load_dotenv
import os
import json
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, 
    LimitOrderRequest, 
    ClosePositionRequest, 
    StopLimitOrderRequest
)
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
from logger import setup_logger  # Import your logger

# Set up logger for TradingUtilities
utilities_logger = setup_logger('Utilities', 'logs/utilities.log')

class TradingUtilities:
    """
    A utility class for managing trading operations using Alpaca API and MongoDB.
    """

    def __init__(self):
        """
        Initializes the TradingUtilities class by loading environment variables,
        setting up the MongoDB client, and initializing the Alpaca trading client.
        """
        load_dotenv()

        # Load configuration
        with open('config.json', 'r') as file:
            self.data = json.load(file)

        # MongoDB Atlas setup
        mongo_uri = os.getenv('MONGO_URI')
        self.client = MongoClient(mongo_uri)
        self.db = self.client['user_db']
        self.webhooks_collection = self.db['webhook_data']

        # Initialize the Alpaca Trading Client
        self.api_key = self.data['APCA-API-KEY-ID']
        self.api_secret = self.data['APCA-API-SECRET-KEY']
        self.trade_client = TradingClient(self.api_key, self.api_secret, paper=True)

        # Global variables to manage state
        self.latest_processed_signal = None
        self.processed_signals = set()

    def calculate_stoploss(self, limit_price, qty):
        total_value = limit_price * qty
        stoploss_value = total_value * 0.10
        stoploss_price = limit_price - stoploss_value
        return stoploss_price

    def cancel_all_open_orders(self):
        try:
            response = self.trade_client.cancel_orders()
            utilities_logger.info("All open orders have been canceled successfully.")
            return response
        except Exception as e:
            utilities_logger.error(f"An error occurred while canceling orders: {e}")
            return None

    def get_all_positions(self):
        try:
            positions = self.trade_client.get_all_positions()
            return positions
        except Exception as e:
            utilities_logger.error(f"An error occurred while retrieving positions: {e}")
            return None

    def get_open_position(self, symbol):
        try:
            position = self.trade_client.get_open_position(symbol_or_asset_id=symbol)
            return position
        except Exception as e:
            utilities_logger.error(f"An error occurred while retrieving the open position for {symbol}: {e}")
            return None

    def get_time_in_force(self, time_in_force):
        mapping = {
            "day": TimeInForce.DAY,
            "gtc": TimeInForce.GTC,
            "opg": TimeInForce.OPG,
            "cls": TimeInForce.CLS,
            "ioc": TimeInForce.IOC,
            "fok": TimeInForce.FOK,
        }
        if time_in_force in mapping:
            return mapping[time_in_force]
        else:
            raise ValueError("Invalid time in force specified.")

    def create_order_request(self, order_type, symbol, qty, order_side, time_in_force, limit_price=None):
        if order_type == "market_order":
            return MarketOrderRequest(symbol=symbol, qty=qty, side=order_side, type=OrderType.MARKET, time_in_force=time_in_force)
        elif order_type == "limit_order":
            if limit_price is None:
                raise ValueError("limitPrice is required for limit orders.")
            return LimitOrderRequest(symbol=symbol, qty=qty, limit_price=limit_price, side=order_side, type=OrderType.LIMIT, time_in_force=time_in_force)
        elif order_type == "stop_loss_limit_order":
            if limit_price is None:
                raise ValueError("Both limitPrice and stoploss are required for stop limit orders.")
            stoploss = self.calculate_stoploss(limit_price, qty)
            return StopLimitOrderRequest(symbol=symbol, qty=qty, side=order_side, time_in_force=time_in_force, limit_price=limit_price, stop_price=stoploss)
        else:
            raise ValueError("Invalid order type specified.")

    def close_position(self, symbol):
        try:
            open_orders = self.get_open_position(symbol)
            if open_orders:
                self.trade_client.close_position(
                    symbol_or_asset_id=symbol,
                    close_options=ClosePositionRequest(qty=str(abs(int(open_orders.qty))))
                )
                utilities_logger.info(f"Position closed for {symbol}.")
            else:
                utilities_logger.warning(f"No open position found for {symbol}.")
        except Exception as e:
            utilities_logger.error(f"An unexpected error occurred while closing position: {e}")

    def determine_position_size(self, symbol, order_price):
        try:
            if order_price <= 0:
                raise ValueError("Order price must be greater than zero.")

            account = self.trade_client.get_account()
            cash_balance = float(account.portfolio_value)
            max_trade_amount = cash_balance * 0.20
            max_quantity_by_value = max_trade_amount // order_price
            position_size = min(max_quantity_by_value, 100)
            return int(position_size)
        except Exception as e:
            utilities_logger.error(f"An error occurred while determining position size for {symbol}: {e}")
            return None
