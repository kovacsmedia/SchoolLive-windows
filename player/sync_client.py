# schoollive_player/sync_client.py
#
# v2 változások:
#   • WsStatus enum: CONNECTING, CONNECTED, TIMEOUT, OFFLINE
#   • on_status_change(WsStatus) callback – UI státuszjelzéshez
#   • 60s timeout: TIMEOUT állapot ha reconnect nem sikerül, de folytatja

import json
import time
import asyncio
import threading
import urllib.request
from enum import Enum, auto
from typing import Optional, Callable
from api_client import SHORT_ID, get_cached_tenant_id, locate_node
from config import get_ws_url, get_api_base, set_api_base

try:
    import websockets
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False


class WsStatus(Enum):
    CONNECTING = auto()
    CONNECTED  = auto()
    TIMEOUT    = auto()
    OFFLINE    = auto()


class ClockSync:
    def __init__(self):
        self._offset_ms = 0.0

    def sync(self) -> None:
        samples = []
        for _ in range(6):
            try:
                t0   = time.monotonic()
                resp = urllib.request.urlopen(f"{get_api_base()}/time", timeout=3)
                t1   = time.monotonic()
                data = json.loads(resp.read())
                rtt_ms = (t1 - t0) * 1000
                if rtt_ms < 300:
                    server_now = data["now"]
                    local_now  = time.time() * 1000
                    samples.append((server_now - local_now - rtt_ms / 2, rtt_ms))
            except Exception:
                pass
            time.sleep(0.1)
        if samples:
            samples.sort(key=lambda x: x[1])
            best = [s[0] for s in samples[:4]]
            best.sort()
            self._offset_ms = best[len(best) // 2]
            print(f"[ClockSync] offset={self._offset_ms:.1f}ms")

    def server_now_ms(self) -> float:
        return time.time() * 1000 + self._offset_ms


class SyncClient:
    CONNECT_TIMEOUT_S = 60

    # A local clock drift HUD-szinkronra elég: 5 percenként ismételjük meg a
    # /time poll-alapú szinkront. A snap-stream saját TIME-szinkronja a
    # snapclient binárison megy (sub-ms), itt csak a WS-üzenet timeline-t
    # frissítjük (playAtMs → delay_ms). Hosszú futás során NTP-pontos
    # gép is 50-200ms-t csúszhat – ez a resync megfogja.
    CLOCK_RESYNC_INTERVAL_S = 300

    def __init__(self,
                 on_prepare:       Callable[[dict], None],
                 on_play:          Callable[[dict], None],
                 on_immediate:     Callable[[dict], None],
                 on_connected:     Optional[Callable]                     = None,
                 on_disconnected:  Optional[Callable]                     = None,
                 on_status_change: Optional[Callable[["WsStatus"], None]] = None,
                 on_device_id:     Optional[Callable[[str], None]]        = None,
                 device_key:       Optional[str]                          = None):
        self._on_prepare       = on_prepare
        self._on_play          = on_play
        self._on_immediate     = on_immediate
        self._on_connected     = on_connected
        self._on_disconnected  = on_disconnected
        self._on_status_change = on_status_change
        self._on_device_id     = on_device_id
        self._device_key       = device_key
        self._ws               = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running          = False
        self._status           = WsStatus.OFFLINE
        self.clock             = ClockSync()
        self._reconnect_delay  = 3
        self._connect_start: Optional[float] = None
        # Multi-node cluster: hány egymást követő reconnect-kísérlet szállt el
        # UGYANAZON hoston – ha ez elér egy küszöböt, feltételezzük hogy a
        # node halott (nem tudott NODE_REASSIGNED-et küldeni), és a
        # /cluster/locate fallbackot próbáljuk.
        self._consecutive_failures = 0

    @property
    def status(self) -> "WsStatus":
        return self._status

    def start(self) -> None:
        if not WS_AVAILABLE:
            self._set_status(WsStatus.OFFLINE)
            return
        self._running       = True
        self._connect_start = time.monotonic()
        self._set_status(WsStatus.CONNECTING)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._launch_timeout_watcher()
        threading.Thread(target=self._clock_resync_loop, daemon=True).start()

    def _clock_resync_loop(self) -> None:
        """Periodikus /time-alapú clock-resync, amíg a kliens fut. Csak akkor
        szinkronizál, ha aktív WS kapcsolatunk van (különben a HELLO úgyis
        visszaállítja az offsetet újrakapcsolódáskor)."""
        while self._running:
            time.sleep(self.CLOCK_RESYNC_INTERVAL_S)
            if not self._running:
                return
            if self._ws is None:
                continue
            try:
                self.clock.sync()
            except Exception as e:
                print(f"[ClockSync] periodikus resync hiba: {e}")

    def stop(self) -> None:
        self._running = False
        self._set_status(WsStatus.OFFLINE)
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def send_ack(self, command_id: str, buffer_ms: int) -> None:
        if self._ws and self._loop:
            msg = json.dumps({
                "type":      "READY_ACK",
                "commandId": command_id,
                "deviceId":  SHORT_ID,
                "bufferMs":  buffer_ms,
                "readyAt":   self._iso_now(),
            })
            asyncio.run_coroutine_threadsafe(self._ws.send(msg), self._loop)

    def send_cmd_ack(self, command_id: str, ok: bool, error: str = "") -> bool:
        """CMD_ACK a WS-en real-time érkezett vezérlő parancsokra (SET_VOLUME/
        MUTE/REBOOT/SHOW_MESSAGE – ld. app.py _on_immediate). Ez zárja ki a
        parancs duplikált végrehajtását, mert a backend a SyncEngine.handleCmdAck
        hívásra ACKED-re állítja a DeviceCommand-ot. Visszatér False-szal, ha
        nincs élő WS kapcsolat (ilyenkor a hívó fél nem tud mit tenni, a
        parancs a következő reconnectkor pushPendingCommands-on újra megérkezik)."""
        if not command_id or not self._ws or not self._loop:
            return False
        body: dict = {"type": "CMD_ACK", "commandId": command_id, "ok": ok}
        if not ok and error:
            body["error"] = error
        asyncio.run_coroutine_threadsafe(self._ws.send(json.dumps(body)), self._loop)
        return True

    def send_beacon(self, volume: int, muted: bool, firmware_version: str, status_payload: dict) -> bool:
        """BEACON a WS-en – az online-státusz/hangerő/mute jelzésének élő
        csatornája, felváltva a korábbi 30s-es HTTP POST /devices/native/beacon
        hívást. A backend SyncEngine.handleBeacon dolgozza fel, BEACON_ACK-kal
        válaszol (jelenleg csak a snapHost/snapPort-ot adja vissza, amit ez a
        kliens már az aktiváláskor megkap – a válasz feldolgozása ezért
        opcionális, ld. _handle "BEACON_ACK" ág)."""
        if not self._ws or not self._loop:
            return False
        body = json.dumps({
            "type":            "BEACON",
            "volume":          volume,
            "muted":           muted,
            "firmwareVersion": firmware_version,
            "statusPayload":   status_payload,
        })
        asyncio.run_coroutine_threadsafe(self._ws.send(body), self._loop)
        return True

    def _set_status(self, status: "WsStatus") -> None:
        if self._status == status:
            return
        self._status = status
        print(f"[SyncClient] Státusz → {status.name}")
        if self._on_status_change:
            self._on_status_change(status)

    def _launch_timeout_watcher(self) -> None:
        start = self._connect_start

        def _watch():
            time.sleep(self.CONNECT_TIMEOUT_S)
            if self._connect_start != start:
                return
            if not self._running:
                return
            if self._status != WsStatus.CONNECTED:
                print(f"[SyncClient] ⏱ {self.CONNECT_TIMEOUT_S}s timeout → TIMEOUT")
                self._set_status(WsStatus.TIMEOUT)

        threading.Thread(target=_watch, daemon=True).start()

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_loop())

    async def _connect_loop(self) -> None:
        while self._running:
            if not self._device_key:
                await asyncio.sleep(5)
                continue

            url = f"{get_ws_url()}?deviceKey={self._device_key}"
            try:
                async with websockets.connect(
                    url,
                    ping_interval=25,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._connect_start = None
                    self._consecutive_failures = 0
                    self._set_status(WsStatus.CONNECTED)
                    print("[SyncClient] ✅ Csatlakozva")
                    asyncio.get_event_loop().run_in_executor(None, self.clock.sync)
                    if self._on_connected:
                        self._on_connected()

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        self._handle(msg)

            except websockets.exceptions.ConnectionClosedError as e:
                if e.code == 4010:
                    print("[SyncClient] 4010 – replaced, 10s várakozás")
                    await asyncio.sleep(10)
                elif e.code == 4009:
                    # Multi-node cluster: a tenant elköltözött. A régi (élő)
                    # node ELŐBB küldött egy NODE_REASSIGNED üzenetet (ld.
                    # _handle) – mire idáig érünk, get_ws_url() már az új
                    # hostot adja vissza, a köv. iteráció automatikusan oda
                    # csatlakozik. Nincs teendő itt, csak a sima (rövid)
                    # reconnect-delay-t hagyjuk érvényesülni.
                    print("[SyncClient] 4009 – tenant másik node-ra költözött")
                else:
                    print(f"[SyncClient] WS bontva: {e}")
            except Exception as e:
                print(f"[SyncClient] WS hiba: {e}")
                self._consecutive_failures += 1
                if self._consecutive_failures >= 4:
                    # Feltehetően a jelenlegi host halott (nem tudott
                    # NODE_REASSIGNED-et küldeni) – /cluster/locate fallback
                    # a cache-elt tenantId alapján.
                    self._consecutive_failures = 0
                    tenant_id = get_cached_tenant_id()
                    if tenant_id:
                        new_host = await self._loop.run_in_executor(None, locate_node, tenant_id)
                        if new_host and new_host != get_api_base().replace("https://", "").replace("http://", "").split("/")[0]:
                            print(f"[SyncClient] /cluster/locate fallback → {new_host}")
                            set_api_base(f"https://{new_host}")
            finally:
                self._ws = None
                if self._status == WsStatus.CONNECTED:
                    if self._on_disconnected:
                        self._on_disconnected()
                    self._connect_start = time.monotonic()
                    self._launch_timeout_watcher()
                    self._set_status(WsStatus.CONNECTING)

            if not self._running:
                break
            await asyncio.sleep(self._reconnect_delay)

    def _handle(self, msg: dict) -> None:
        if msg.get("type") == "NODE_REASSIGNED":
            # Multi-node cluster: a régi (élő) node ezt küldi el, MIELŐTT a
            # rebalancing miatt lezárná a kapcsolatot (4009 close code követi).
            # Azonnal átállítjuk a base URL-t – a _connect_loop köv. iterációja
            # (get_ws_url()) magától az új host felé fog csatlakozni.
            new_host = msg.get("hostname")
            if new_host:
                print(f"[SyncClient] NODE_REASSIGNED → {new_host}")
                set_api_base(f"https://{new_host}")
            return

        if msg.get("type") == "HELLO":
            try:
                self.clock._offset_ms = int(msg["serverNowMs"]) - time.time() * 1000
            except Exception:
                pass
            # ESP32/native (deviceKey-auth) kliensnél a HELLO deviceId mezője
            # a tényleges Device.id (a backend a deviceKey-ből oldja fel) –
            # ezt korábban csak a HTTP beacon válaszából ismertük meg.
            device_id = msg.get("deviceId")
            if device_id and self._on_device_id:
                try:
                    self._on_device_id(str(device_id))
                except Exception:
                    pass
            return

        phase = msg.get("phase")
        if phase == "PREPARE":
            self._on_prepare(msg)
        elif phase == "PLAY":
            self._on_play(msg)
        elif msg.get("action"):
            self._on_immediate(msg)

    @staticmethod
    def _iso_now() -> str:
        import datetime
        return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"