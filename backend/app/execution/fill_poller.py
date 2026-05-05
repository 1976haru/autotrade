import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter
from app.db.models import OrderAuditLog


logger = logging.getLogger(__name__)


_OPEN_STATUSES = ("RECEIVED", "PARTIALLY_FILLED")


async def poll_once(broker: BrokerAdapter, db: Session) -> int:
    """Update audit rows whose broker fill state may have advanced.

    Looks at OrderAuditLog rows where executed is True, broker_order_id is set,
    and broker_status is still RECEIVED or PARTIALLY_FILLED. Calls
    broker.get_order_status for each, writes back changed fields, and commits
    once at the end. Returns the number of rows actually changed.

    A NotImplementedError from the broker (e.g. a Mock that does not support
    get_order_status) ends the loop quietly — there is nothing to poll for
    that adapter. Other errors are logged and the next row is tried.
    """
    candidates = db.execute(
        select(OrderAuditLog).where(
            OrderAuditLog.executed.is_(True),
            OrderAuditLog.broker_order_id.isnot(None),
            OrderAuditLog.broker_status.in_(_OPEN_STATUSES),
        )
    ).scalars().all()

    updated = 0
    for audit in candidates:
        try:
            result = await broker.get_order_status(audit.broker_order_id)
        except NotImplementedError:
            return 0
        except Exception as e:
            logger.warning("fill poll failed for audit %s: %s", audit.id, e)
            continue

        new_status = result.status.value
        new_filled = result.filled_quantity
        new_avg = result.avg_fill_price

        if (
            audit.broker_status == new_status
            and audit.filled_quantity == new_filled
            and (new_avg is None or audit.avg_fill_price == new_avg)
        ):
            continue

        audit.broker_status = new_status
        audit.filled_quantity = new_filled
        if new_avg is not None:
            audit.avg_fill_price = new_avg
        updated += 1

    if updated > 0:
        db.commit()
    return updated


class FillPoller:
    """Background task that calls poll_once on a fixed interval.

    The broker_factory and session_factory callables are invoked on every
    tick, so the poller naturally picks up the current configured broker
    (mock vs KIS) and an isolated DB session.
    """

    def __init__(
        self,
        broker_factory:  Callable[[], BrokerAdapter],
        session_factory: Callable[[], Session],
        interval:        int = 5,
    ):
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.broker_factory  = broker_factory
        self.session_factory = session_factory
        self.interval        = interval
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                broker = self.broker_factory()
                with self.session_factory() as db:
                    await poll_once(broker, db)
            except Exception as e:
                logger.warning("fill poller tick raised: %s", e)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=self.interval + 1)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None
