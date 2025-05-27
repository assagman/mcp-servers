import os
import sys
import logging
from typing import Optional, Dict, Union
import logging.config

from colorama import Fore, Style, init as colorama_init
colorama_init(autoreset=True)

_DEFAULT_LOGGER_NAME = "mcp_servers"

class ColoredFormatter(logging.Formatter):
    """
    A custom formatter that adds color to log messages for improved readability.

    This formatter applies different colors to timestamps, logger names, and log levels,
    making it easier to visually parse log output in terminal environments.

    Attributes:
        COLORS (Dict[str, str]): Mapping of log components to their respective colors.
        DEFAULT_FORMAT (str): Default format string for log messages, primarily for reference
                              as this formatter manually constructs the output string.
    """

    COLORS: Dict[str, str] = {
        'timestamp': Fore.LIGHTYELLOW_EX,
        'name': Fore.LIGHTGREEN_EX,
        'DEBUG': Fore.LIGHTCYAN_EX,
        'INFO': Fore.CYAN,
        'WARNING': Fore.YELLOW,
        'ERROR': Fore.LIGHTRED_EX,
        'CRITICAL': Fore.RED
    }

    DEFAULT_FORMAT = "[%(levelname)s] - %(name)s - %(asctime)s: %(message)s"

    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None):
        """
        Initialize the ColoredFormatter.

        Args:
            fmt (Optional[str]): Optional custom format string. If None, uses DEFAULT_FORMAT.
                                 Note: The custom format() method largely dictates the output structure.
            datefmt (Optional[str]): Optional date format string.
                                     Note: The custom format() method hardcodes timestamp formatting.
        """
        super().__init__(fmt or self.DEFAULT_FORMAT, datefmt)
        self.use_colors = self._should_use_colors()

    @staticmethod
    def _should_use_colors() -> bool:
        """
        Determine if colors should be used based on the environment.

        Returns:
            bool: True if colors should be used, False otherwise.
        """
        # Check if output is a TTY
        if not hasattr(sys.stdout, 'isatty') or not sys.stdout.isatty():
            return False

        # Check for NO_COLOR environment variable (https://no-color.org/)
        if os.environ.get('NO_COLOR'):
            return False

        # Check for FORCE_COLOR environment variable
        if os.environ.get('FORCE_COLOR'):
            return True

        # Check if running in CI environment (common CI env vars)
        ci_env_vars = ['CI', 'CONTINUOUS_INTEGRATION', 'GITHUB_ACTIONS', 'JENKINS']
        if any(os.environ.get(var) for var in ci_env_vars):
            return True # Often CI environments support color or capture it.

        return True # Default to using colors if it's a TTY and not disabled.

    def format(self, record: logging.LogRecord) -> str:
        """
        Format the specified record with colors.

        Args:
            record (logging.LogRecord): The log record to format.

        Returns:
            str: The formatted log message with color codes (if colors are enabled).
        """
        # Format the timestamp with millisecond precision, as per the original class snippet
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S,%f")[:-3]
        levelname = record.levelname
        name = record.name
        message = record.getMessage()  # Ensures any args are formatted into message

        if self.use_colors:
            colored_timestamp = f"{self.COLORS['timestamp']}{timestamp}{Style.RESET_ALL}"
            colored_name = f"{self.COLORS['name']}{name}{Style.RESET_ALL}"
            level_color = self.COLORS.get(levelname, Fore.WHITE) # Default color if level not in COLORS
            colored_level = f"{level_color}{levelname}{Style.RESET_ALL}"
        else:
            colored_timestamp = timestamp
            colored_name = name
            colored_level = levelname

        exc_text = ""
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            if exc_text:
                exc_text = f"\n{exc_text}"

        # Construct the formatted log message based on desired structure
        formatted_message = f"[{colored_level}] - {colored_name} - {colored_timestamp}: {message}{exc_text}"

        return formatted_message



class UvicornAccessFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        try:
            request_part = message.split('"')[1]
            # status_code = message.split()[-2]
            method, path, _ = request_part.split(" ", 2)
            # Customize your criteria here
            should_log = (
                method == "POST" and "/messages/" not in path
                # or int(status_code) >= 400
            )
            return should_log
        except (IndexError, ValueError):
            return False

class HttpxFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        try:
            should_log = (
                "POST" in message # and "/messages/" in path
                and "messages" not in message
                # or int(status_code) >= 400
            )
            return should_log
        except (IndexError, ValueError):
            return False


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "colored": {
            "()": ColoredFormatter,  # Use your custom formatter
            "fmt": ColoredFormatter.DEFAULT_FORMAT,  # Optional, as format() overrides it
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "colored",
            "level": "DEBUG",
        },
    },
    "loggers": {
        "mcp_servers": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
        },
        "AbstractMCPServer": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
        },
        "mcp.server.lowlevel.server": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
        },
        "uvicorn": {
            "level": "WARNING",
            "handlers": ["console"],
            "propagate": False,
        },
        "uvicorn.access": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
            "filters": ["uvicorn_access_filter"],
        },
        "uvicorn.error": {
            "level": "WARNING",
            "handlers": ["console"],
            "propagate": False,
        },
        "httpx": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
            "filters": ["httpx_messages_filter"],
        },
    },
    "filters": {
        "uvicorn_access_filter": {
            "()": UvicornAccessFilter,
        },
        "httpx_messages_filter": {
            "()": HttpxFilter,
        },
    },
    "root": {
        "level": "WARNING",
        "handlers": ["console"],
    },
}

logging.config.dictConfig(LOGGING_CONFIG)



class MCPServersLogger:
    @staticmethod
    def get_logger(
        logger_name: str = _DEFAULT_LOGGER_NAME,
        level: Optional[Union[int, str]] = logging.INFO,
        log_file: Optional[str] = None,
        propagate: bool = False  # Set to False for the main app logger to avoid duplicate root logs
    ) -> logging.Logger:
        log = logging.getLogger(logger_name)

        log.setLevel(level) # type: ignore
        log.propagate = propagate

        # Clear any existing handlers to prevent duplication and allow reconfiguration
        log.handlers.clear()

        # Console Handler with ColoredFormatter
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(ColoredFormatter())
        log.addHandler(console_handler)

        # Optional File Handler
        if log_file:
            try:
                file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
                # For files, a more detailed, non-colored format is often preferred.
                file_formatter = logging.Formatter(
                    "%(asctime)s - %(name)s - [%(levelname)s] - %(message)s (%(filename)s:%(lineno)d)"
                )
                file_handler.setFormatter(file_formatter)
                log.addHandler(file_handler)
            except Exception as e:
                log.error(f"Failed to set up file handler for logger '{logger_name}' at '{log_file}': {e}", exc_info=True)

        return log
