# -*- encoding: utf-8 -*-
# Arbitrage API

import os
import time
import logging
from web3 import Web3
from pathlib import Path
from dotenv import load_dotenv

from api.util import hex_to_int, wei_to_eth, send_request, \
                        craft_url, open_abi, format_price, \
                        save_results, format_path, create_dir, \
                        format_filename, get_time_now, format_perc


class ArbitrageAPI(object):

    def __init__(self) -> None:

        self.tokens_address = {
            'WETH': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
            'DAI': '0x6b175474e89094c44da98b954eedeac495271d0f'
        }
        self.exchanges_address = {
            'UNISWAP': '0xa478c2975ab1ea89e8196811f51a7b7ade33eb11',
            'SUSHISWAP': '0xc3d03e4f041fd4cd388c549ee2a29a9e5075882f',
            'SHEBASWAP': '0x8faf958e36c6970497386118030e6297fff8d275',
            'SAKESWAP': '0x2ad95483ac838e2884563ad278e933fba96bc242',
            'CROSWAP': '0x60a26d69263ef43e9a68964ba141263f19d71d51'
        }

        self.current_balances = {}
        self.current_balances_web3 = {}
        self.current_price_data = {}
        self.arbitrage_result = []
        self.provider_url = None
        self.w3_obj = None
        self.result_dir = None
        self.trading_qty = 0
        self.arbitrage_threshold = 0

        self._load_config()

    def _load_config(self) -> None:

        load_dotenv(Path('.') / '.env')

        ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
        ALCHEMY_URL = os.getenv("ALCHEMY_URL")
        TRADING_QTY = os.getenv("TRADING_QTY")
        ARBITRAGE_THRESHOLD = os.getenv("ARBITRAGE_THRESHOLD")
        RESULT_DIR = os.getenv("RESULT_DIR")
        MIN_HEALTHY_POOL = os.getenv("MIN_HEALTHY_POOL")

        if not (bool(ALCHEMY_URL) and bool(ALCHEMY_API_KEY) and
                bool(TRADING_QTY) and bool(ARBITRAGE_THRESHOLD)
                and bool(RESULT_DIR) and bool(MIN_HEALTHY_POOL)):
            raise Exception('🚨 Please add info to env file')

        self.result_dir = RESULT_DIR
        self.trading_qty = float(TRADING_QTY)
        self.min_healthy_pool = MIN_HEALTHY_POOL
        self.arbitrage_threshold = float(ARBITRAGE_THRESHOLD)
        self.provider_url = craft_url(ALCHEMY_URL, ALCHEMY_API_KEY)

    def _get_balance_for_wallet(self, wallet_address, token_obj) -> float:

        balance_wei = token_obj.functions.balanceOf(wallet_address).call()
        return float(self.w3_obj.fromWei(balance_wei, 'ether'))

    def get_balance_through_web3_lib(self) -> None:

        self.w3_obj = Web3(Web3.HTTPProvider(self.provider_url))

        for exchange, contract in self.exchanges_address.items():
            self.current_balances_web3[exchange] = {}
            exchange_address = self.w3_obj.toChecksumAddress(contract)

            for token, contract in self.tokens_address.items():

                abi = open_abi(f'./docs/{token}-abi.json')
                address = self.w3_obj.toChecksumAddress(contract)
                token_obj = self.w3_obj.eth.contract(address=address, abi=abi)

                self.current_balances_web3[exchange][token] = \
                    self._get_balance_for_wallet(exchange_address, token_obj)

    def set_quantity(self, qty) -> None:

        try:
            self.trading_qty = float(qty)
        except ValueError as e:
            logging.error(f'🚨 Using default quantity for tokens: {e}')

    def get_block_number(self) -> dict:

        data = '{"jsonrpc":"2.0", "id":"1", "method": "eth_blockNumber"}'
        response = send_request(self.provider_url, data)

        if response:
            try:
                eth_blockNumber_hex = response['result']
                return hex_to_int(eth_blockNumber_hex)
            except TypeError:
                logging.exception('\n🚨 Check whether the request is valid.}')

    def get_token_balance(self, token, exchange) -> str:

        token_address = self.tokens_address[token]
        exchange_address = self.exchanges_address[exchange][2:]

        data = '{"jsonrpc": "2.0", "method": "eth_call", "params":' + \
            '[{"data": "' + \
            '0x70a08231000000000000000000000000' + \
            exchange_address + \
            '", "to": "' + \
            token_address + \
            '"}, "latest"], "id": 1}'

        response = send_request(self.provider_url, data)
        try:
            return wei_to_eth(hex_to_int(response['result']))
        except KeyError:
            logging.error(f'\n🚨 Retrieved data is ill-formatted: {response}')

    def get_all_balances(self) -> None:

        for exchange in self.exchanges_address.keys():
            self.current_balances[exchange] = {}

            for token in self.tokens_address.keys():
                self.current_balances[exchange][token] = \
                    self.get_token_balance(token, exchange)

    def _calculate_price_data(self, token1_bal, token2_bal, qty) -> float:

        CONSTANT_PRODUCT = token1_bal * token2_bal
        CURRENT_PRICE = token2_bal / token1_bal

        # Calculate buy data
        token1_bal_buy = CONSTANT_PRODUCT / (token2_bal + qty)
        t1_amount_out_buy = token1_bal - token1_bal_buy
        buy_price = qty / t1_amount_out_buy
        impact_buy = 1 - (CURRENT_PRICE / buy_price)

        # Calculate sell data
        token2_bal_buy = CONSTANT_PRODUCT / (token1_bal + qty)
        t2_amount_out_buy = token2_bal + token2_bal_buy
        token1_bal_sell = CONSTANT_PRODUCT / (token2_bal - qty)
        t1_amount_in_sell = token1_bal + token1_bal_sell
        sell_price = t2_amount_out_buy / t1_amount_in_sell
        impact_sell = 1 - (CURRENT_PRICE / sell_price)

        return [format_price(CURRENT_PRICE), format_price(buy_price),
                format_price(sell_price), format_perc(impact_buy),
                format_perc(impact_sell), CONSTANT_PRODUCT]

    def get_pair_prices(self, token, pair_token, qty=None) -> None:

        qty = qty or self.trading_qty
        for exchange in self.exchanges_address.keys():

            token_balance = self.current_balances[exchange][token]
            pair_token_balance = self.current_balances[exchange][pair_token]

            price_data = self._calculate_price_data(token_balance,
                                                pair_token_balance, qty)

            if float(price_data[5]) <= float(self.min_healthy_pool):
                self.current_price_data[exchange] = {
                    'current_price': price_data[0],
                    'info': "Pool's unbalanced for at least one token.",
                    'balance_constant': price_data[5],
                    'balance_t1': self.current_balances[exchange][token],
                    'balance_t2': self.current_balances[exchange][pair_token]
                }
            else:
                self.current_price_data[exchange] = {
                    'current_price': price_data[0],
                    'buy_price': price_data[1],
                    'sell_price': price_data[2],
                    'impact_buy': price_data[3],
                    'impact_sell': price_data[4],
                    'info': get_time_now(),
                    'balance_constant': price_data[5]
                }

    def get_arbitrage(self) -> list:

        self.get_all_balances()

        # TODO: generalize to any pair input
        self.get_pair_prices('WETH', 'DAI')

        exchange_list = [item[0] for item in self.current_prices.items()]
        buy_price = float('inf')
        sell_price = 0
        buy_exchange = None
        sell_exchange = None
        data = []

        while exchange_list:
            exchange_here = exchange_list.pop()

            buy_price_here = float(self.current_prices[exchange_here][0])
            # TODO: handle smaller quantity better (negative price)
            if buy_price_here < buy_price and buy_price_here > 0:
                buy_price = buy_price_here
                buy_exchange = exchange_here
                continue

            sell_price_here = float(self.current_prices[exchange_here][1])
            if sell_price_here > sell_price:
                sell_price = sell_price_here
                sell_exchange = exchange_here
                continue

        # TODO: re-add options for multiple arbitrages in this loop
        arbitrage = buy_price_here - sell_price_here
        if arbitrage > self.arbitrage_threshold:
            details = f"Buy at {sell_exchange} at {sell_price} and "
            details = details + f"sell at {buy_exchange} at {buy_price}"
            data = [arbitrage, details]
            self.arbitrage_result.append(data)

        # TODO: remove hardcoded DAI (add token name)
        return f'Arbitrage: {arbitrage} DAI: ' + details

    def run_algorithm(self, runtime) -> None:

        results = []
        loop = 0
        runtime = 60 * runtime
        end = time.time() + runtime

        while time.time() < end:

            data = self.get_arbitrage()
            if data:
                print(f'    Loop {loop}: {data}')
                results.append(data)
            loop += loop + 1

            time.sleep(5)

        create_dir(self.result_dir)
        destination = format_path(self.result_dir, format_filename())

        save_results(destination, results)
