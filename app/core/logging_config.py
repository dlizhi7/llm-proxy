import logging


def get_logger(name: str = "proxy") -> logging.Logger:
    """Configure and return a shared logger.

    Centralizing logger setup avoids duplicated basicConfig calls across modules.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(name)
