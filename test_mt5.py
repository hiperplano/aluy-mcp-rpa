import sys
print("start", flush=True)
try:
    import MetaTrader5 as mt5
    print("imported", flush=True)

    if not mt5.initialize():
        err = mt5.last_error()
        print(f"init failed: {err}", flush=True)
        sys.exit(1)

    print("connected", flush=True)
    info = mt5.account_info()
    print(f"account: {info.login}, balance: {info.balance}", flush=True)

    symbols = mt5.symbols_get()
    for s in symbols:
        if 'BTC' in s.name.upper() or 'ETH' in s.name.upper():
            print(f"CRYPTO: {s.name}", flush=True)

    mt5.shutdown()
    print("done", flush=True)
except Exception as e:
    print(f"error: {e}", flush=True)
    import traceback
    traceback.print_exc()
