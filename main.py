from flask import Flask, render_template, request, jsonify, redirect, url_for
from pymongo import MongoClient
import json
import requests
import os
from bson import ObjectId
from threading import Event, Thread
import datetime
import time
from alpaca.trading.client import TradingClient
from utilities import TradingUtilities
from logger import setup_logger  # Import your logger

# Set up logger for TradingApp
app_logger = setup_logger('TradingApp', 'logs/trading_app.log')

class MongoEncoder(json.JSONEncoder):
    def default(self, obj):
        """Encode MongoDB ObjectId to string."""
        if isinstance(obj, ObjectId):
            return str(obj)
        return super().default(obj)

class TradingApp:
    """
    A Flask web application for monitoring and executing trades using Alpaca API.
    """

    def __init__(self):
        """Initializes the TradingApp by loading configurations, setting up database connections,
        and initializing the Flask application.
        """
        self.app = Flask(__name__)
        self.stop_event = Event()

        # Load configuration
        with open('config.json', 'r') as file:
            self.data = json.load(file)

        # Initialize MongoDB client
        mongo_uri = os.getenv('MONGO_URI')
        self.client = MongoClient(mongo_uri)
        self.db = self.client['user_db']
        self.webhooks_collection = self.db['webhook_data']

        # Initialize Alpaca Trading Client
        self.trade_client = TradingClient(self.data['APCA-API-KEY-ID'], self.data['APCA-API-SECRET-KEY'], paper=True)

        # Setup routes
        self.setup_routes()

    def setup_routes(self):
        """Set up Flask routes for the application."""
        self.app.add_url_rule('/', 'index', self.index)
        self.app.add_url_rule('/login', 'login', self.login, methods=['GET', 'POST'])
        self.app.add_url_rule('/webhook', 'webhook', self.webhook, methods=['POST'])
        self.app.add_url_rule('/start_trade', 'start_trade', self.start_trade, methods=['POST'])
        self.app.add_url_rule('/stop_trade', 'stop_trade', self.stop_trade, methods=['POST'])

    def index(self):
        """Render the index page with closed orders and balance data."""
        activity_url = "https://paper-api.alpaca.markets/v2/account/activities?direction=desc&page_size=100"
        headers = {
            "accept": "application/json",
            "APCA-API-KEY-ID": self.data['APCA-API-KEY-ID'],
            "APCA-API-SECRET-KEY": self.data['APCA-API-SECRET-KEY']
        }

        act_response = requests.get(activity_url, headers=headers)
        closed_orders = []

        if act_response.status_code == 200:
            orders_data = act_response.json()
            closed_orders = [
                {
                    'asset': order['symbol'],
                    'side': order['side'],
                    'type': order['type'],
                    'status': order['order_status'],
                    'quantity': order['qty'],
                    'submitted': order['transaction_time'],
                    'filled_at': order['transaction_time']
                }
                for order in orders_data if order['activity_type'] == 'FILL'
            ]
        else:
            app_logger.error(f"Failed to retrieve activities: {act_response.text}")

        # Fetch portfolio history
        portfolio_url = "https://paper-api.alpaca.markets/v2/account/portfolio/history"
        portfolio_response = requests.get(portfolio_url, headers=headers)
        balance_data = []

        if portfolio_response.status_code == 200:
            portfolio_history = portfolio_response.json()
            timestamps = portfolio_history['timestamp']
            equities = portfolio_history['equity']
            profits = portfolio_history['profit_loss']
            profit_loss_pct = portfolio_history['profit_loss_pct']

            balance_data = [
                {
                    'timestamp': datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'),
                    'equity': eq,
                    'profit_loss': pl,
                    'profit_loss_pct': pl_pct
                }
                for ts, eq, pl, pl_pct in zip(timestamps, equities, profits, profit_loss_pct)
            ]
            balance_data.reverse()
        else:
            app_logger.error(f"Failed to retrieve portfolio history: {portfolio_response.text}")

        return render_template('index.html', clos_orders=closed_orders, balance_data=balance_data)

    def login(self):
        """Handle user login and update trading preferences."""
        if request.method == 'POST':
            time_in_force = request.form.get('time_in_force')
            order_type = request.form.get('order_type')

            self.data["orderType"] = order_type
            self.data["timeInForce"] = time_in_force

            with open('config.json', 'w') as file:
                json.dump(self.data, file, indent=4)

            app_logger.info("Updated trading preferences.")
            return redirect(url_for('index'))
        return render_template('login.html')

    def webhook(self):
        """Receive and process webhook messages."""
        webhook_message = json.loads(request.data)

        if webhook_message['passphrase'] != "somelongstring123":
            return {'code': 'error', 'message': 'Unauthorized'}, 403

        try:
            self.webhooks_collection.insert_one(webhook_message)
            app_logger.info("Webhook message received and processed.")
        except Exception as e:
            app_logger.error(f"Failed to insert webhook message: {e}")
            return jsonify({'code': 'error', 'message': str(e)}), 500

        response = json.dumps(webhook_message, cls=MongoEncoder)
        return response

    def monitor_and_trade(self):
        """Monitor signals and execute trades based on received signals."""
        current_positions = {}

        try:
            while not self.stop_event.is_set():
                signals = list(self.webhooks_collection.find().sort([('_id', -1)]).limit(10))
                for signal in signals:
                    signal_id = signal['_id']
                    symbol = signal['ticker']
                    action = signal['strategy']['order_action']
                    order_price = signal['strategy']['order_price']

                    # Process buy signals
                    if action == 'buy':
                        qty = TradingUtilities().determine_position_size(symbol, order_price)
                        if qty is None or qty <= 0:
                            app_logger.warning(f"Invalid position size for buying {symbol}. Skipping.")
                            continue

                        order_type = self.data["orderType"]
                        time_in_force = TradingUtilities().get_time_in_force(self.data["timeInForce"])
                        req = TradingUtilities().create_order_request(order_type, symbol, qty, 'buy', time_in_force, order_price)
                        res = self.trade_client.submit_order(req)
                        current_positions[symbol] = 'long'
                        app_logger.info(f"Executed buy order for {symbol} with quantity {qty}.")

                    # Process sell signals
                    elif action == 'sell':
                        qty = TradingUtilities().determine_position_size(symbol, order_price)
                        if qty is None or qty <= 0:
                            app_logger.warning(f"Invalid position size for selling {symbol}. Skipping.")
                            continue

                        order_type = self.data["orderType"]
                        time_in_force = TradingUtilities().get_time_in_force(self.data["timeInForce"])
                        req = TradingUtilities().create_order_request(order_type, symbol, qty, 'sell', time_in_force, order_price)
                        res = self.trade_client.submit_order(req)
                        current_positions[symbol] = 'short'
                        app_logger.info(f"Executed sell order for {symbol} with quantity {qty}.")

                    # After processing, delete the signal from the database
                    self.webhooks_collection.delete_one({'_id': signal['_id']})

                time.sleep(1)

        except Exception as e:
            app_logger.error(f"An error occurred while monitoring signals: {e}")
            time.sleep(2)

    def start_trade(self):
        """Start the trading thread to monitor and execute trades."""
        if self.stop_event.is_set():
            self.stop_event.clear()
        trade_thread = Thread(target=self.monitor_and_trade)
        trade_thread.start()
        app_logger.info("Trade monitoring started.")
        return jsonify({'status': 'success', 'message': 'Trade monitoring started'}), 200

    def stop_trade(self):
        """Stop the trading thread and close all positions."""
        self.trade_client.close_all_positions()
        self.stop_event.set()
        app_logger.info("Trade monitoring stopped.")
        return jsonify({'status': 'success', 'message': 'Trade monitoring stopped'}), 200

    def run(self):
        """Run the Flask application."""
        self.app.run(debug=True)

if __name__ == '__main__':
    trading_app = TradingApp()
    trading_app.run()
