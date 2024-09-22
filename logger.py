import logging
import os

def setup_logger(name, log_file, level=logging.INFO):
    """Sets up a logger with specified name and log file."""
    if not os.path.exists('logs'):
        os.makedirs('logs')

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = logging.FileHandler(log_file)
    handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(handler)
    return logger


