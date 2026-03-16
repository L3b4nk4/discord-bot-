"""Legacy compatibility entrypoint.

Runs the same startup flow as `main.py`.
"""
import asyncio

from main import main


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

