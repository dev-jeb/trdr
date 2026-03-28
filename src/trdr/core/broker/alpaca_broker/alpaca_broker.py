from opentelemetry.trace.status import Status, StatusCode
from typing import List, Dict
from datetime import datetime
import os
from decimal import Decimal

from ..base_broker import BaseBroker
from ..models import Order, Position, OrderStatus, OrderSide, OrderType
from ...shared.models import Money
from ...shared.models import TradingDateTime


class AlpacaBroker(BaseBroker):

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        """Disabled constructor - use AlpacaBroker.create() instead."""
        raise TypeError("Use AlpacaBroker.create() instead to create a new broker")

    async def _initialize(self):
        with self._tracer.start_as_current_span("alpaca_broker._initialize") as span:
            # expect api key and base url to be present as environment variables
            api_key = os.getenv("ALPACA_API_KEY")
            secret_key = os.getenv("ALPACA_SECRET_KEY")
            base_url = os.getenv("ALPACA_BASE_URL")
            if not api_key or not secret_key or not base_url:
                span.add_event("missing_environment_variables")
                span.set_status(
                    Status(StatusCode.ERROR, "ALPACA_API_KEY, ALPACA_SECRET_KEY, and ALPACA_BASE_URL must be set")
                )
                raise ValueError("ALPACA_API_KEY, ALPACA_SECRET_KEY, and ALPACA_BASE_URL must be set")
            if "paper" not in base_url:
                span.add_event("using_live_trading")
                print("ATTENTION: YOU ARE USING A LIVE TRADING ACCOUNT")

            self._headers = {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": secret_key,
            }
            self._base_url = base_url
            return

    async def _get_account_info(self) -> Dict:
        """Fetch account information from Alpaca API."""
        with self._tracer.start_as_current_span("alpaca_broker._get_account_info") as span:
            try:
                async with self._session.get(f"{self._base_url}/v2/account", headers=self._headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        span.set_attribute("error.type", "http_error")
                        span.set_attribute("error.status_code", response.status)
                        span.set_attribute("error.message", error_text)
                        span.set_status(
                            Status(StatusCode.ERROR, f"Failed to get account info: {response.status} {error_text}")
                        )
                        raise Exception(f"Failed to get account info: {response.status} {error_text}")

                    account_info = await response.json()
                    span.set_status(Status(StatusCode.OK))
                    return account_info
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    async def _refresh_cash(self):
        with self._tracer.start_as_current_span("alpaca_broker._refresh_cash") as span:
            try:
                account_info = await self._get_account_info()
                cash_value = account_info.get("cash", None)
                if cash_value is None:
                    span.add_event("cash is None")
                    span.set_status(Status(StatusCode.OK))
                    return
                self._cash = Money(amount=Decimal(cash_value), currency="USD")
                span.set_attribute("cash", str(self._cash))
                span.set_status(Status(StatusCode.OK))
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    async def _refresh_equity(self):
        with self._tracer.start_as_current_span("alpaca_broker._refresh_equity") as span:
            try:
                account_info = await self._get_account_info()
                equity_value = account_info.get("equity", None)
                if equity_value is None:
                    span.add_event("equity is None")
                    span.set_status(Status(StatusCode.OK))
                    return
                self._equity = Money(amount=Decimal(equity_value), currency="USD")
                span.set_attribute("equity", str(self._equity))
                span.set_status(Status(StatusCode.OK))
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    async def _refresh_day_trade_count(self):
        with self._tracer.start_as_current_span("alpaca_broker._refresh_day_trade_count") as span:
            try:
                account_info = await self._get_account_info()
                day_trade_count = account_info.get("daytrade_count", None)
                if day_trade_count is None:
                    span.add_event("day_trade_count is None")
                    span.set_status(Status(StatusCode.OK))
                    return
                self._day_trade_count = int(day_trade_count)
                span.set_attribute("day_trade_count", day_trade_count)
                span.set_status(Status(StatusCode.OK))
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    async def _get_orders(self, symbols: List[str], submitted_after: TradingDateTime | None = None) -> List[Dict]:
        with self._tracer.start_as_current_span("alpaca_broker._get_orders") as span:
            span.set_attribute("symbols.count", len(symbols))
            span.set_attribute("symbols", ",".join(symbols))

            try:
                if not symbols:
                    span.add_event("no_symbols_provided")
                    span.set_status(Status(StatusCode.OK))
                    raise ValueError("No symbols provided")

                if submitted_after:
                    submitted_after_str = submitted_after.timestamp.strftime("%Y-%m-%dT%H:%M:%S%z")
                    span.set_attribute("submitted_after", submitted_after_str)
                    span.add_event("getting_orders_after", {"submitted_after": submitted_after_str})
                    url = f"{self._base_url}/v2/orders?symbols={",".join(symbols)}&status=all&limit=500&submitted_after={submitted_after_str}"
                else:
                    span.add_event("getting_all_orders")
                    url = f"{self._base_url}/v2/orders?symbols={",".join(symbols)}&status=all&limit=500"

                async with self._session.get(url, headers=self._headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        span.set_attribute("error.type", "http_error")
                        span.set_attribute("error.status_code", response.status)
                        span.set_attribute("error.message", error_text)
                        span.set_status(
                            Status(StatusCode.ERROR, f"Failed to get orders: {response.status} {error_text}")
                        )
                        raise Exception(f"Failed to get orders: {response.status} {error_text}")

                    orders = await response.json()
                    span.set_attribute("orders.count", len(orders))
                    span.set_status(Status(StatusCode.OK))
                    return orders
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    async def _refresh_positions(self):
        with self._tracer.start_as_current_span("alpaca_broker._refresh_positions") as span:
            span.set_attribute("broker.type", "alpaca")
            try:
                # Initialize positions dictionary if not already initialized
                if self._positions is None:
                    self._positions = {}

                # Get current positions from Alpaca
                async with self._session.get(f"{self._base_url}/v2/positions", headers=self._headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        span.set_attribute("error.type", "http_error")
                        span.set_attribute("error.status_code", response.status)
                        span.set_attribute("error.message", error_text)
                        span.set_status(
                            Status(StatusCode.ERROR, f"Failed to get positions: {response.status} {error_text}")
                        )
                        raise Exception(f"Failed to get positions: {response.status} {error_text}")

                    list_of_positions = await response.json()
                    span.set_attribute("positions.count", len(list_of_positions))

                    # Extract symbols from positions
                    list_of_symbols = [position["symbol"] for position in list_of_positions]

                    if not list_of_symbols:
                        span.add_event("no_positions_found")
                        span.set_status(Status(StatusCode.OK))
                        return

                    # Get orders for these positions
                    list_of_orders = await self._get_orders(list_of_symbols)

                    # Process positions and their orders
                    while list_of_orders is not None:
                        span.add_event("processing_batch_of_orders", {"count": len(list_of_orders)})

                        for symbol in list_of_symbols:
                            # Convert Alpaca orders to our Order model
                            orders_for_symbol = []
                            for alpaca_order in [o for o in list_of_orders if o["symbol"] == symbol]:
                                try:
                                    order = self._convert_alpaca_order_to_model(alpaca_order)
                                    orders_for_symbol.append(order)
                                except Exception as e:
                                    span.record_exception(e)
                                    span.add_event("order_conversion_failed", {"symbol": symbol, "error": str(e)})
                                    raise

                            # Create or update position
                            existing_position = self._positions.get(symbol, None)
                            if existing_position:
                                existing_position.orders.extend(orders_for_symbol)
                                span.add_event(
                                    "updated_position", {"symbol": symbol, "orders_added": len(orders_for_symbol)}
                                )
                            else:
                                # Find matching position from Alpaca data
                                alpaca_position = next((p for p in list_of_positions if p["symbol"] == symbol), None)
                                if alpaca_position:
                                    position = self._convert_alpaca_position_to_model(
                                        alpaca_position, orders_for_symbol
                                    )
                                    self._positions[symbol] = position
                                    span.add_event("created_position", {"symbol": symbol})

                        # Check if we need to paginate (Alpaca API limit is 500 orders per request)
                        if len(list_of_orders) == 500:
                            last_order = list_of_orders[-1]
                            parsed_datetime = datetime.strptime(last_order["submitted_at"], "%Y-%m-%dT%H:%M:%S%z")
                            submitted_at = TradingDateTime.from_utc(parsed_datetime)
                            span.add_event("fetching_more_orders", {"after_datetime": str(submitted_at)})
                            list_of_orders = await self._get_orders(list_of_symbols, submitted_at)
                        else:
                            list_of_orders = None

                span.set_status(Status(StatusCode.OK))
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    def _convert_alpaca_order_to_model(self, alpaca_order: Dict) -> Order:
        """Convert Alpaca API order format to our Order model."""
        with self._tracer.start_as_current_span("alpaca_broker._convert_alpaca_order_to_model") as span:
            try:
                # Map Alpaca order statuses to our OrderStatus enum
                status_mapping = {
                    "new": OrderStatus.PENDING,
                    "filled": OrderStatus.FILLED,
                    "partially_filled": OrderStatus.PARTIAL_FILL,
                    "canceled": OrderStatus.CANCELED,
                    "expired": OrderStatus.CANCELED,
                    "rejected": OrderStatus.REJECTED,
                    "pending_cancel": OrderStatus.CANCELED,
                }

                # Map Alpaca order side to our OrderSide enum
                side_mapping = {"buy": OrderSide.BUY, "sell": OrderSide.SELL}

                # Map Alpaca order type to our OrderType enum
                type_mapping = {
                    "market": OrderType.MARKET,
                }

                # Extract required fields
                symbol = alpaca_order["symbol"]
                side = side_mapping.get(alpaca_order["side"].lower(), None)
                if side is None:
                    span.add_event("side is None")
                    span.set_status(Status(StatusCode.ERROR, "Side is None"))
                    raise ValueError("Side is None")
                order_type = type_mapping.get(alpaca_order["type"].lower(), None)
                if order_type is None:
                    span.add_event("order_type is None")
                    span.set_status(Status(StatusCode.ERROR, "Order type is None"))
                    raise ValueError("Order type is None")

                quantity_requested = Decimal(alpaca_order["qty"])

                filled_price = Decimal(alpaca_order.get("filled_avg_price", None))
                filled_qty = Decimal(alpaca_order.get("filled_qty", None))

                created_at = None
                if "submitted_at" in alpaca_order and alpaca_order["submitted_at"]:
                    timestamp = alpaca_order["submitted_at"].replace("Z", "+0000")
                    created_at = TradingDateTime.from_utc(datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%f%z"))

                filled_at = None
                if "filled_at" in alpaca_order and alpaca_order["filled_at"]:
                    timestamp = alpaca_order["filled_at"].replace("Z", "+0000")
                    filled_at = TradingDateTime.from_utc(datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%f%z"))

                status = status_mapping.get(alpaca_order["status"].lower(), None)
                if status is None:
                    span.add_event("status is None")
                    span.set_status(Status(StatusCode.ERROR, "Status is None"))
                    raise ValueError("Status is None")

                # Parse strategy name from client_order_id
                client_order_id = alpaca_order.get("client_order_id", "")
                strategy_name = client_order_id.rsplit(":", 1)[0] if ":" in client_order_id else "unknown"

                # Create our Order model
                order = Order(
                    symbol=symbol,
                    side=side,
                    quantity_requested=quantity_requested if quantity_requested else None,
                    quantity_filled=filled_qty if filled_qty else None,
                    avg_fill_price=Money(amount=filled_price) if filled_price else None,
                    type=order_type,
                    status=status,
                    created_at=created_at,
                    filled_at=filled_at,
                    client_order_id=client_order_id,
                    strategy_name=strategy_name,
                )

                span.set_status(Status(StatusCode.OK))
                return order

            except Exception as e:
                span.record_exception(e)
                span.set_attribute("error.order_id", alpaca_order.get("id", "unknown"))
                span.set_status(Status(StatusCode.ERROR, f"Error converting order: {str(e)}"))
                raise ValueError(f"Failed to convert Alpaca order: {str(e)}")

    def _convert_alpaca_position_to_model(self, alpaca_position: Dict, orders: List[Order]) -> Position:
        """Convert Alpaca API position format to our Position model."""
        with self._tracer.start_as_current_span("alpaca_broker._convert_alpaca_position_to_model") as span:
            try:
                symbol = alpaca_position["symbol"]

                # Create our Position model
                position = Position(
                    symbol=symbol,
                    orders=orders,
                )

                span.set_status(Status(StatusCode.OK))
                return position

            except Exception as e:
                span.record_exception(e)
                span.set_attribute("error.symbol", alpaca_position.get("symbol", "unknown"))
                span.set_status(Status(StatusCode.ERROR, f"Error converting position: {str(e)}"))
                raise ValueError(f"Failed to convert Alpaca position: {str(e)}")

    async def _place_order(self, order: Order) -> None:
        with self._tracer.start_as_current_span("alpaca_broker._place_order") as span:
            span.add_event(
                "placing_market_order with quantity requested",
                {"symbol": order.symbol, "quantity_requested": str(order.quantity_requested)},
            )

            payload = {
                "symbol": order.symbol,
                "qty": str(order.quantity_requested),
                "side": order.side.value,
                "type": order.type.value,
                "time_in_force": order.time_in_force,
                "client_order_id": order.client_order_id,
            }

            async with self._session.post(
                f"{self._base_url}/v2/orders", headers=self._headers, json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    span.set_attribute("error.type", "http_error")
                    span.set_attribute("error.status_code", response.status)
                    span.set_attribute("error.message", error_text)
                    span.set_status(Status(StatusCode.ERROR, f"Failed to place order: {response.status} {error_text}"))
                    raise Exception(f"Failed to place order: {response.status} {error_text}")

                order_info = await response.json()
                span.set_status(Status(StatusCode.OK))
                return

    async def _cancel_all_orders(self, strategy_name: str) -> None:
        with self._tracer.start_as_current_span("alpaca_broker._cancel_all_orders") as span:
            try:
                span.set_attribute("strategy_name", strategy_name)
                async with self._session.get(
                    f"{self._base_url}/v2/orders",
                    headers=self._headers,
                    params={"status": "open"},
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        span.set_status(Status(StatusCode.ERROR, f"Failed to fetch open orders: {error_text}"))
                        raise Exception(f"Failed to fetch open orders: {response.status} {error_text}")
                    open_orders = await response.json()

                cancelled_count = 0
                for order in open_orders:
                    client_order_id = order.get("client_order_id", "")
                    if client_order_id.startswith(f"{strategy_name}:"):
                        order_id = order["id"]
                        async with self._session.delete(
                            f"{self._base_url}/v2/orders/{order_id}", headers=self._headers
                        ) as cancel_response:
                            if cancel_response.status in (200, 204):
                                cancelled_count += 1
                            else:
                                span.add_event(
                                    "order_cancellation_failed",
                                    {"order_id": order_id, "status": cancel_response.status},
                                )

                span.set_attribute("cancelled_orders.count", cancelled_count)
                span.set_status(Status(StatusCode.OK))

            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise
