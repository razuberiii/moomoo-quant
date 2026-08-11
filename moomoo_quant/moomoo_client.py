import logging
import socket
from contextlib import contextmanager
from datetime import date
from typing import Iterable

import pandas as pd
from moomoo import (
    OpenQuoteContext,
    RET_OK,
)

from . import config

logger = logging.getLogger(__name__)


class MoomooApiError(RuntimeError):
    pass


def ensure_opend_reachable(timeout: float = 3.0) -> None:
    try:
        with socket.create_connection((config.OPEND_HOST, config.OPEND_PORT), timeout=timeout):
            return
    except OSError as exc:
        raise MoomooApiError(
            f"OpenD is not reachable at {config.OPEND_HOST}:{config.OPEND_PORT}: {exc}"
        ) from exc


@contextmanager
def quote_context():
    ctx = OpenQuoteContext(host=config.OPEND_HOST, port=config.OPEND_PORT)
    try:
        yield ctx
    finally:
        logger.info("Closing quote context")
        ctx.close()


def _require_ok(ret_code: int, payload, action: str):
    if ret_code != RET_OK:
        raise MoomooApiError(f"{action} failed: {payload}")
    return payload


def get_market_snapshot(codes: Iterable[str]) -> pd.DataFrame:
    with quote_context() as ctx:
        ret, data = ctx.get_market_snapshot(list(codes))
        data = _require_ok(ret, data, "get_market_snapshot")
    return data


def fetch_history_kline(
    code: str,
    start: str,
    end: str | None = None,
    max_count: int = config.MAX_KLINE_COUNT,
    ktype: str | None = None,
    autype: str | None = None,
) -> pd.DataFrame:
    end = end or date.today().isoformat()
    frames: list[pd.DataFrame] = []
    page_req_key = None

    with quote_context() as ctx:
        while True:
            ret, data, page_req_key = ctx.request_history_kline(
                code=code,
                start=start,
                end=end,
                ktype=ktype or config.KLINE_TYPE,
                autype=autype or config.ADJUST_TYPE,
                max_count=max_count,
                page_req_key=page_req_key,
            )
            data = _require_ok(ret, data, "request_history_kline")
            if not data.empty:
                frames.append(data)
                logger.info("Fetched %s rows for %s", len(data), code)
            if page_req_key is None:
                break

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["time_key"]).sort_values("time_key")
    return df.reset_index(drop=True)
