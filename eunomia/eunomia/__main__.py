import logging

from .app import Eunomia
from .config import Config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # caldav logs whole requests (URLs and bodies) below INFO,
    # so we keep it above that to prevent leaking the password
    logging.getLogger("caldav").setLevel(logging.WARNING)

    Eunomia(Config.from_env()).run()


if __name__ == "__main__":
    main()
