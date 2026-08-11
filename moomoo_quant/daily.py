import logging

from filelock import FileLock, Timeout

from . import config
from .backtest.trend_analysis import run_cached_trend_analysis, run_full_trend_analysis
from .moomoo_client import ensure_opend_reachable
from .shadow import LOCK_FILE, update_shadow

logger = logging.getLogger(__name__)


def run_daily() -> dict:
    config.SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(config.SHADOW_DIR / LOCK_FILE, timeout=0)
    try:
        with lock:
            logger.info("Daily update started")
            ensure_opend_reachable()
            analysis = run_full_trend_analysis()
            state = update_shadow(analysis["daily"], analysis["full"]["signals"])
            logger.info("Daily update finished: %s", state["status"])
            return state
    except Timeout as exc:
        raise RuntimeError("Another daily process is already running") from exc


def initialize_shadow_from_cache() -> dict:
    config.SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(config.SHADOW_DIR / LOCK_FILE, timeout=0)
    try:
        with lock:
            logger.info("Cached shadow initialization started")
            analysis = run_cached_trend_analysis()
            state = update_shadow(analysis["daily"], analysis["full"]["signals"])
            logger.info("Cached shadow initialization finished: %s", state["status"])
            return state
    except Timeout as exc:
        raise RuntimeError("Another daily process is already running") from exc
