from flask import Flask, render_template, request, jsonify, redirect, url_for
from utilities import *
from pymongo import MongoClient
import json, requests, os
from bson import ObjectId
from threading import Event
import datetime
import time
from alpaca.trading.client import TradingClient





class MongoEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        return json.JSONEncoder.default(self, obj)
    
with open('config.json', 'r') as file:
    data = json.load(file)

api_key = data['APCA-API-KEY-ID']
api_secret = data['APCA-API-SECRET-KEY']

mongo_uri = os.getenv('MONGO_URI', )
client = MongoClient(mongo_uri)
db = client['user_db']
users_collection = db['users']
broker_details = db['broker_details']
webhooks_collection = db['webhook_data']


trade_client = TradingClient(api_key, api_secret, paper=True, url_override=None)
WEBHOOK_PASSPHRASE = "somelongstring123"


app = Flask(__name__)
stop_event = Event()

@app.route('/')
def index():
    activity_url = "https://paper-api.alpaca.markets/v2/account/activities?direction=desc&page_size=100"
    
    headers = {
        "accept": "application/json",
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret
    }

    act_response = requests.get(activity_url, headers=headers)

    if act_response.status_code == 200:
        orders_data = act_response.json()
        closed_orders = []

        for order in orders_data:
            if order['activity_type'] == 'FILL': 
                closed_orders.append({
                    'asset': order['symbol'],
                    'side': order['side'],
                    'type': order['type'],
                    'status': order['order_status'],
                    'quantity': order['qty'],
                    'submitted': order['transaction_time'],  
                    'filled_at': order['transaction_time']  
                })
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

        for i in range(len(timestamps)):
            balance_data.append({
                'timestamp': datetime.datetime.fromtimestamp(timestamps[i]).strftime('%Y-%m-%d %H:%M:%S'),
                'equity': equities[i],
                'profit_loss': profits[i],
                'profit_loss_pct': profit_loss_pct[i]
            })

        balance_data.reverse()



    return render_template('index.html', clos_orders=closed_orders, balance_data=balance_data)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Handle login logic here (e.g., verify credentials)
        time_in_force = request.form.get('time_in_force')
        order_type = request.form.get('order_type')

        data["orderType"] =  order_type
        data["timeInForce"]=  time_in_force
        

        with open('config.json', 'w') as file:
            json.dump(data, file, indent=4)

        # print(f"Time In Force: {time_in_force}, Order Type: {order_type}")

        return redirect(url_for('index'))
    return render_template('login.html')  # Show the login form for GET requests
  


# Webhook Listener
@app.route('/webhook', methods=['POST'])
def webhook():
    webhook_message = json.loads(request.data)

    print(webhook_message)
    

    if webhook_message['passphrase'] != WEBHOOK_PASSPHRASE:
        return {'code': 'error', 'message': 'nice try buddy'}, 403
        
    try:
        webhooks_collection.insert_one(webhook_message)
    except Exception as e:
        return jsonify({'code': 'error', 'message': str(e)}), 500
    
    response = json.dumps(webhook_message, cls=MongoEncoder)
    return response



# Function to monitor and execute trades based on signals
def monitor_and_trade():
    global latest_processed_signal, processed_signals
    current_positions = {} 

    try:
        while not stop_event.is_set():

            signals = list(webhooks_collection.find().sort([('_id', -1)]).limit(10))
            print(f"this is the signal:-    {signals}")
            for signal in signals:
                signal_id = signal['_id']
                symbol = signal['ticker']
                action = signal['strategy']['order_action']
                order_price = signal['strategy']['order_price']

                if signal_id in processed_signals:
                    continue  
            

                # Process buy signals
                if action == 'buy':
                    
                    try:
                        open_orders = get_open_position(symbol)
                    except Exception as e:
                        error_message = str(e)
                        if error_message:
                            print(f"No open position exists for {symbol}, proceeding with signal.")
                            open_orders = None  
                        else:
                            if open_orders.side.value == 'short':
                                close_position(symbol)
                            print(f"An error occurred while retrieving the open position for {symbol}: {error_message}")
                            continue

                    # if open_orders:
                    #     print(f"Skipping buy signal for {symbol} as an order is already open.")
                    #     continue
                    
                    qty = determine_position_size(symbol, order_price)
                    if qty is None or qty <= 0:
                        print(f"Invalid position size for buying {symbol}. Skipping.")
                        continue

                    order_type = data["orderType"]
                    time_in_force = data["timeInForce"]
                    time_in_force = get_time_in_force(time_in_force)

                    
                    req = create_order_request(order_type, symbol, qty, 'buy', time_in_force, order_price)
                    res = trade_client.submit_order(req)

                    current_positions[symbol] = 'long'
                    print(f"Executed buy order for {symbol} with quantity {qty}.")


                # Process sell signals
                elif action == 'sell':

                    try:
                        open_orders = get_open_position(symbol)
                    except Exception as e:
                        error_message = str(e)
                        if error_message:
                            print(f"No open position exists for {symbol}, proceeding with signal.")
                            open_orders = None  
                        else:
                            if open_orders.side.value == 'long':
                                close_position(symbol)
                            print(f"An error occurred while retrieving the open position for {symbol}: {error_message}")
                            continue

                    # if open_orders:
                    #     print(f"Skipping sell signal for {symbol} as an order is already open.")
                    #     continue


                    order_type = data["orderType"]
                    time_in_force = data["timeInForce"]
                    time_in_force = get_time_in_force(time_in_force)
                    qty = determine_position_size(symbol, order_price)
                    if qty is None or qty <= 0:
                        print(f"Invalid position size for buying {symbol}. Skipping.")
                        continue

                    req = create_order_request(order_type, symbol, qty, 'sell', time_in_force, order_price)
                    res = trade_client.submit_order(req)

                    current_positions[symbol] = 'short'
                    print(f"Executed sell order for {symbol} with quantity {qty}.")

                # After processing, you might want to remove the signal from the database
                webhooks_collection.delete_one({'_id': signal['_id']})

            time.sleep(1)

    except Exception as e:
        print(f"An error occurred while monitoring signals: {e}")
        time.sleep(2) 

@app.route('/start_trade', methods=['POST'])
def start_trade():
    global stop_event
    webhooks_collection.delete_many({})
    if stop_event.is_set():
        stop_event.clear()
    trade_thread = Thread(target=monitor_and_trade)
    trade_thread.start()
    return jsonify({'status': 'success', 'message': 'Trade monitoring started'}), 200

@app.route('/stop_trade', methods=['POST'])
def stop_trade():
    global stop_event
    webhooks_collection.delete_many({})
    trade_client.close_all_positions()
    stop_event.set()
    return jsonify({'status': 'success', 'message': 'Trade monitoring stopped'}), 200


if __name__ == '__main__':
    app.run(debug=True, port = 8050, host='0.0.0.0')

