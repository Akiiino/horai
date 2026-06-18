import logging

from .app import Eunomia
from .config import Config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs (containing the bot token) at INFO,
    # so we keep logs above INFO to prevent leaking
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    Eunomia(Config.from_env()).run()


if __name__ == "__main__":
    main()
