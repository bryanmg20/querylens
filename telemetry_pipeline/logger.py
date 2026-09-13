import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler

LOGS_DIR = Path(__file__).resolve().parent / "logs"

def get_logger(name: str) -> logging.Logger:
    LOGS_DIR.mkdir(exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            RotatingFileHandler(
                LOGS_DIR / "pipeline.log",
                maxBytes=5*1024*1024,
                backupCount=3
            ),
        ]
    )
    
    return logging.getLogger(name)