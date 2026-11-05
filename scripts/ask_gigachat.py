import argparse
import asyncio

from app.config import Settings
from app.gigachat import GigaChatClient, GigaChatError


async def ask(prompt):
    async with GigaChatClient(Settings()) as client:
        print(await client.ask(prompt))


def main():
    parser = argparse.ArgumentParser(description="Send a text request to GigaChat")
    parser.add_argument("prompt")
    args = parser.parse_args()
    try:
        asyncio.run(ask(args.prompt))
    except (GigaChatError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
