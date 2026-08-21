import argparse


def add_arguments(parser):
    parser.add_argument('-l', '--locale', default='en',
                        help='Trade Republic locale, default en')
    parser.add_argument('-c', '--currency', default='EUR',
                        help='Trade Republic currency, default EUR')
    parser.add_argument('--allow-orders', action='store_true',
                        help='Enable live order placement and cancellation')
    parser.add_argument('--allow-watchlist', action='store_true',
                        help='Enable adding and removing instruments from the watchlist')
    parser.add_argument('--allow-savings-plans', action='store_true',
                        help='Enable live savings-plan creation, changes, and cancellation')


def get_arguments():
    parser = argparse.ArgumentParser(
                        prog='pytr-mcp',
                        description='Launch the Trade Republic MCP server.')
    add_arguments(parser)
    return parser.parse_args()
