"""Explicit local offers-only entrypoint; importing this file starts nothing."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def build_offers_only_app(argv: list[str]):
    parser = argparse.ArgumentParser(description="Local offers-only ASGI host")
    for name in ("quote-key-file", "offers-db-path", "data-dir", "config-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True)
    args = parser.parse_args(argv)
    if args.host != "127.0.0.1" or not args.port.isdecimal() or not 1 <= int(args.port) <= 65535:
        raise ValueError("literal loopback host and numeric port required")
    key, database, data, config = (Path(value) for value in
                                   (args.quote_key_file, args.offers_db_path, args.data_dir, args.config_dir))
    if any(not path.is_absolute() or ".." in path.parts for path in (key, database, data, config)):
        raise ValueError("explicit absolute offers paths required")
    if database != data / "offers-only.duckdb" or key.parent != config:
        raise ValueError("owned offers paths required")
    os.environ["OFFLINE"] = "1"
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["PROCURE_DATA_DIR"] = str(data)
    os.environ["PROCURE_DB_PATH"] = str(database)
    os.environ["PROCURE_CONFIG_DIR"] = str(config)
    for name in tuple(os.environ):
        if (name.endswith("_API_KEY") or name in {"GOOGLE_APPLICATION_CREDENTIALS",
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}):
            os.environ.pop(name, None)
    from procure import db
    db.enable_offers_only_strict()
    from procure.offers.local_app import create_offers_only_app
    app = create_offers_only_app(db_path=database, key_file=key, data_dir=data,
                                 config_dir=config)
    app.listen_port = int(args.port)
    return app


def main() -> None:
    import sys
    app = build_offers_only_app(sys.argv[1:])
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=app.listen_port, workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()
