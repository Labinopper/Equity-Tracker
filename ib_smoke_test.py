"""
IB API smoke test — connects to TWS (Paper) and requests live market data for IBM.
Auto-disconnects after 10 seconds. No orders placed.
"""

import threading
import time

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper


class IBApp(EWrapper, EClient):
    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)
        self._connected = threading.Event()

    # --- Connection callbacks ---

    def nextValidId(self, orderId: int):
        print("Connected (nextValidId received)")
        self._connected.set()
        self._request_market_data()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        # 2104/2106/2158 are informational "market data farm connected" messages
        informational = {2104, 2106, 2158, 2176}
        prefix = "Info" if errorCode in informational else "Error"
        print(f"{prefix} [{reqId}] {errorCode}: {errorString}")

    # --- Market data callbacks ---

    def tickPrice(self, reqId, tickType, price, attrib):
        if price > 0:
            print(f"tickPrice  reqId={reqId}  tickType={tickType}  price={price}")

    def tickSize(self, reqId, tickType, size):
        print(f"tickSize   reqId={reqId}  tickType={tickType}  size={size}")

    # --- Helpers ---

    def _request_market_data(self):
        contract = Contract()
        contract.symbol = "IBM"
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"

        # genericTickList="" requests default ticks; snapshot=False = streaming
        self.reqMktData(reqId=1, contract=contract, genericTickList="", snapshot=False, regulatorySnapshot=False, mktDataOptions=[])
        print("Market data requested for IBM")


def main():
    app = IBApp()

    try:
        app.connect("127.0.0.1", 4002, clientId=1)
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    # Run the EClient message loop in a background thread
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()

    # Wait up to 5 s for the connection handshake
    if not app._connected.wait(timeout=5):
        print("Timed out waiting for nextValidId — check TWS is running on 127.0.0.1:4002")
        app.disconnect()
        return

    # Let market data stream for 10 seconds
    time.sleep(10)

    print("Disconnecting...")
    app.disconnect()
    thread.join(timeout=3)
    print("Done.")


if __name__ == "__main__":
    main()
