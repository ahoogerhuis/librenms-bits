"""Real WinrmExecutor: pypsrp (genuine PowerShell Remoting Protocol) +
Kerberos, targeting a specific JEA-registered PSSessionConfiguration on
the remote host.

This is deliberately NOT built on pywinrm, despite that being the
originally planned dependency. Verified directly against pywinrm's
actual source before writing this: pywinrm's open_shell() hardcodes
the generic WinRS `cmd` shell resource URI with no way to target a
named PSSessionConfiguration, and its command execution model spawns
processes (e.g. powershell.exe -encodedcommand ...) inside that
generic shell rather than implementing real PSRP pipeline invocation.
Using pywinrm as planned would have meant the proxy's Kerberos-
authenticated service account could run arbitrary commands as a normal
remote shell process -- completely bypassing JEA, which is supposed to
be the actual security boundary here. pypsrp (jborean93/pypsrp)
implements the real PSRP protocol, including
RunspacePool(..., configuration_name=...) to connect to a specific
named JEA endpoint -- confirmed against its actual installed source
(pypsrp.powershell.RunspacePool.__init__, PowerShell class), not just
its docs.

Concurrency limiting + explicit timeouts (2026-08-10). The /check
endpoint is a sync FastAPI handler -- each in-flight call ties up a
worker thread for its full duration, including however long a hung
connection attempt takes. With no bound, many hosts going dark at once
(a firewall change, a DC outage affecting Kerberos fleet-wide) can
exhaust every worker thread, making the proxy unresponsive even to
healthy hosts -- a real cascading-failure shape, not a hypothetical
one. A threading.Semaphore (this runs on FastAPI's sync threadpool,
not asyncio -- asyncio.Semaphore would be the wrong primitive here,
it's not thread-safe across OS threads) bounds how many outbound
connection attempts can be in flight at once. Pairs with
connection_queue_timeout_seconds: a semaphore alone still lets threads
queue indefinitely waiting for a slot under sustained overload, so
acquiring the semaphore itself is bounded too, failing loud rather
than hanging.

pypsrp's own WSMan timeout defaults (operation_timeout=20,
connection_timeout=30, read_timeout=30) were previously left
completely implicit -- discovered by reading pypsrp's actual
WSMan.__init__ signature, not documentation. Now threaded through
explicitly from Settings so they're visible and tunable in our own
config, not a silent dependency on a third-party library's defaults
never changing.

Session pooling (2026-08-10, opt-in via session_pooling_enabled).
Previously every call tore down and rebuilt its WSMan/RunspacePool
from scratch -- a full Kerberos handshake and PSRP session negotiation
per check, even against the same host every poll cycle. Reusing a
live RunspacePool per host avoids that overhead, at the cost of three
real correctness problems named up front in
docs/WINRM_PROXY_CONCURRENCY.md (librenms-fork) rather than discovered
mid-implementation:

- Staleness: a pooled session can go bad silently (network blip,
  target reboot, JEA endpoint re-registered). Handled here by
  optimistic reuse -- try the pooled session, and on *any* failure
  evict it and retry once with a fresh one, rather than a separate
  health-check call. This can't distinguish "the pooled session was
  actually dead" from "the target genuinely failed this call" -- a
  real remote failure gets retried once too, harmlessly (the retry
  fails the same way and that failure propagates normally).
- Thread-safety: whether pypsrp's RunspacePool safely supports
  concurrent PowerShell/pipeline invocations from multiple threads is
  unverified against its source, so this doesn't assume it does. A
  per-host threading.Lock serializes use of that host's pooled
  session; the *global* connection semaphore above still governs
  overall concurrency across hosts.
- Sizing/eviction: max_pooled_sessions bounds total pooled sessions
  (LRU eviction of the least-recently-used host when over the cap);
  pooled_session_idle_timeout_seconds evicts sessions idle longer than
  that, checked lazily at the start of each call rather than via a
  background thread. Actual socket teardown for evicted sessions runs
  on a short-lived daemon thread, not inline in the request that
  triggered eviction -- so a request that happens to trigger a sweep
  or an LRU eviction never blocks on closing someone else's stale
  connection.

Default: session_pooling_enabled=False. Opt-in, not on-by-default --
the same "defaults preserve prior behavior exactly" approach already
used for the concurrency-limiting settings.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

from pypsrp.powershell import PowerShell, RunspacePool
from pypsrp.wsman import WSMan

from app.winrm_executor import ExecutorError


class _PooledSession:
    __slots__ = ("wsman", "pool", "lock", "last_used")

    def __init__(self, wsman: WSMan, pool: RunspacePool) -> None:
        self.wsman = wsman
        self.pool = pool
        self.lock = threading.Lock()
        self.last_used = time.monotonic()


class WinrmExecutorPypsrp:
    def __init__(
        self,
        jea_configuration_name: str = "WinrmProbe",
        port: int = 5985,
        ssl: bool = False,
        max_concurrent_connections: int = 10,
        connection_queue_timeout_seconds: int = 30,
        operation_timeout_seconds: int = 20,
        connection_timeout_seconds: int = 30,
        read_timeout_seconds: int = 30,
        session_pooling_enabled: bool = False,
        max_pooled_sessions: int = 50,
        pooled_session_idle_timeout_seconds: int = 600,
    ) -> None:
        # port=5985/ssl=False by default: the real test target
        # (winrm-test01.vpp.local) only has WinRM/HTTP open, not
        # HTTPS/5986 -- confirmed by direct port check, not assumed.
        # This is a normal, expected setup for Kerberos-authenticated
        # WinRM: Kerberos provides message-level encryption at the
        # WSMan/SOAP layer independent of transport-level TLS, so
        # plain HTTP is not "unencrypted" here the way it would be for
        # e.g. basic auth. See docs/WINRM_TRANSPORT_SECURITY.md
        # (librenms-fork) for the full mechanism, verified against
        # pypsrp's actual source, not assumed.
        self.jea_configuration_name = jea_configuration_name
        self.port = port
        self.ssl = ssl
        self.connection_queue_timeout_seconds = connection_queue_timeout_seconds
        self.operation_timeout_seconds = operation_timeout_seconds
        self.connection_timeout_seconds = connection_timeout_seconds
        self.read_timeout_seconds = read_timeout_seconds

        # One shared semaphore per executor instance -- main.py
        # constructs exactly one WinrmExecutorPypsrp and reuses it
        # across every request, so this is genuinely global across the
        # whole proxy process, not per-request/per-host. A per-host
        # limiter would protect against hammering one host but
        # wouldn't, by itself, prevent the actual failure mode this
        # exists for: many *different* hosts going dark simultaneously,
        # each grabbing its own slot, still exhausting every worker
        # thread. Global is the direct fix for that specific shape.
        self._connection_semaphore = threading.Semaphore(max_concurrent_connections)

        self.session_pooling_enabled = session_pooling_enabled
        self.max_pooled_sessions = max_pooled_sessions
        self.pooled_session_idle_timeout_seconds = pooled_session_idle_timeout_seconds
        # OrderedDict doubles as the LRU structure: move_to_end() on
        # access, popitem(last=False) evicts the least-recently-used
        # entry. Guarded by _pool_lock, which is held only for pure
        # dict bookkeeping -- never across a network call -- so it
        # can't become a fleet-wide bottleneck serializing every host's
        # first connection behind one lock.
        self._pool_lock = threading.Lock()
        self._pooled_sessions: "OrderedDict[str, _PooledSession]" = OrderedDict()

    def invoke_function(self, host: str, function_name: str) -> str:
        if self.session_pooling_enabled:
            self._sweep_idle_sessions()

        acquired = self._connection_semaphore.acquire(timeout=self.connection_queue_timeout_seconds)
        if not acquired:
            raise ExecutorError(
                f"proxy at maximum concurrent connections, {function_name} on {host} "
                f"did not get a connection slot within {self.connection_queue_timeout_seconds}s"
            )

        try:
            if self.session_pooling_enabled:
                return self._invoke_pooled(host, function_name)
            return self._invoke_fresh(host, function_name)
        finally:
            # Released whether the call succeeded, failed, or hung and
            # timed out inside pypsrp -- this slot must always come back
            # for the semaphore to actually bound anything.
            self._connection_semaphore.release()

    def _invoke_fresh(self, host: str, function_name: str) -> str:
        """Original, non-pooled behavior: a brand-new WSMan/RunspacePool
        per call, torn down at the end regardless of outcome.
        """
        try:
            wsman = WSMan(
                host,
                port=self.port,
                ssl=self.ssl,
                auth="kerberos",
                encryption="auto",
                operation_timeout=self.operation_timeout_seconds,
                connection_timeout=self.connection_timeout_seconds,
                read_timeout=self.read_timeout_seconds,
            )
            with wsman, RunspacePool(wsman, configuration_name=self.jea_configuration_name) as pool:
                return self._run_on_pool(pool, host, function_name)
        except ExecutorError:
            raise
        except Exception as e:
            raise ExecutorError(f"WinRM call to {host} failed: {e}")

    def _invoke_pooled(self, host: str, function_name: str) -> str:
        session = self._get_or_create_pooled_session(host)
        try:
            with session.lock:
                return self._run_on_pool(session.pool, host, function_name)
        except ExecutorError:
            # Can't tell "the pooled session was actually dead" apart
            # from "the target genuinely failed this call" from here --
            # evict and retry once either way. A second failure is real
            # and propagates normally.
            self._evict_session(host, session)

        fresh = self._get_or_create_pooled_session(host)
        with fresh.lock:
            return self._run_on_pool(fresh.pool, host, function_name)

    def _run_on_pool(self, pool: RunspacePool, host: str, function_name: str) -> str:
        try:
            with PowerShell(pool) as ps:
                # No .add_parameter() calls at all -- the whitelisted
                # functions take zero arguments by design (see
                # WINRM_JEA_WINDOWS.md), so there is no parameter
                # surface to construct here in the first place.
                ps.add_cmdlet(function_name)
                output = ps.invoke()

                if ps.had_errors:
                    messages = "; ".join(str(e) for e in ps.streams.error)
                    raise ExecutorError(
                        f"{function_name} on {host} failed: {messages or 'unknown error'}"
                    )
        except ExecutorError:
            raise
        except Exception as e:
            # Anything else -- connection failure, Kerberos auth
            # failure, JEA configuration_name not found/rejected,
            # function not in VisibleFunctions, etc. All become
            # ExecutorError so callers never need to know which layer
            # actually failed.
            raise ExecutorError(f"WinRM call to {host} failed: {e}")

        if not output:
            raise ExecutorError(f"{function_name} on {host} returned no output")

        return str(output[0])

    def _get_or_create_pooled_session(self, host: str) -> _PooledSession:
        with self._pool_lock:
            session = self._pooled_sessions.get(host)
            if session is not None:
                self._pooled_sessions.move_to_end(host)
                session.last_used = time.monotonic()
                return session

        # Build outside the lock -- this is a real Kerberos handshake
        # and PSRP session negotiation, and holding _pool_lock for its
        # duration would serialize every other host's first connection
        # behind whichever host happens to be cold-starting.
        try:
            wsman = WSMan(
                host,
                port=self.port,
                ssl=self.ssl,
                auth="kerberos",
                encryption="auto",
                operation_timeout=self.operation_timeout_seconds,
                connection_timeout=self.connection_timeout_seconds,
                read_timeout=self.read_timeout_seconds,
            )
            wsman.__enter__()
            pool = RunspacePool(wsman, configuration_name=self.jea_configuration_name)
            pool.__enter__()
        except Exception as e:
            raise ExecutorError(f"WinRM call to {host} failed: {e}")

        session = _PooledSession(wsman, pool)
        victims: list[_PooledSession] = []

        with self._pool_lock:
            existing = self._pooled_sessions.get(host)
            if existing is not None:
                # Lost a race with another thread that built this same
                # host's session concurrently -- close what we just
                # built, use theirs.
                victims.append(session)
                session = existing
                self._pooled_sessions.move_to_end(host)
            else:
                self._pooled_sessions[host] = session
                while len(self._pooled_sessions) > self.max_pooled_sessions:
                    _, evicted = self._pooled_sessions.popitem(last=False)
                    victims.append(evicted)
            session.last_used = time.monotonic()

        self._close_sessions_async(victims)
        return session

    def _evict_session(self, host: str, session: _PooledSession) -> None:
        with self._pool_lock:
            if self._pooled_sessions.get(host) is session:
                del self._pooled_sessions[host]
        self._close_sessions_async([session])

    def _sweep_idle_sessions(self) -> None:
        now = time.monotonic()
        victims: list[_PooledSession] = []
        with self._pool_lock:
            stale_hosts = [
                host
                for host, s in self._pooled_sessions.items()
                if now - s.last_used > self.pooled_session_idle_timeout_seconds
            ]
            for host in stale_hosts:
                victims.append(self._pooled_sessions.pop(host))

        self._close_sessions_async(victims)

    def _close_sessions_async(self, victims: list[_PooledSession]) -> None:
        if not victims:
            return
        # Fire-and-forget on a daemon thread: closing a pooled session
        # is a real network round-trip (and can hang, bounded only by
        # this executor's own timeout settings, if the connection is
        # genuinely dead) -- it must never add that latency to whatever
        # unrelated request happened to trigger the eviction/sweep.
        threading.Thread(target=self._close_sessions_sync, args=(victims,), daemon=True).start()

    @staticmethod
    def _close_sessions_sync(victims: list[_PooledSession]) -> None:
        for session in victims:
            try:
                session.pool.__exit__(None, None, None)
            except Exception:
                pass
            try:
                session.wsman.__exit__(None, None, None)
            except Exception:
                pass
