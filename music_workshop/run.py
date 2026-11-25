"""Launch the independent local music workshop."""
import argparse

import uvicorn


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8022)
    args = parser.parse_args()
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)
