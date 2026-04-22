"""WeChat iLink-Bot channel.

QR-code login, long-poll ``getupdates`` loop, and media decryption for
WeChat personal accounts exposed through the iLink gateway. The crypto
helpers (``_parse_ilink_aes_key`` / ``_aes_ecb_decrypt``) live here
because the only caller that needs them is
:meth:`WeChatChannel._download_cdn_media`.
"""
from __future__ import annotations

import base64
import json
import logging
import random
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pip_agent.channels.base import (
    Attachment,
    Channel,
    InboundMessage,
    _detect_image_mime,
)

log = logging.getLogger(__name__)


def _parse_ilink_aes_key(raw: str) -> bytes:
    """Decode an iLink AES key (3 possible formats) into 16 raw bytes."""
    import binascii
    if len(raw) == 32:
        try:
            return binascii.unhexlify(raw)
        except ValueError:
            pass
    decoded = base64.b64decode(raw)
    if len(decoded) == 16:
        return decoded
    # base64(hex-string) → hex string → bytes
    try:
        return binascii.unhexlify(decoded)
    except (ValueError, binascii.Error):
        return decoded[:16]


def _aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    """AES-128-ECB decrypt with tolerant PKCS7 unpadding.

    Follows hermes-agent strategy: return raw plaintext if padding is
    malformed rather than raising, since some CDN blobs may lack proper
    padding.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    if not padded:
        return padded
    pad_len = padded[-1]
    if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
        return padded[:-pad_len]
    return padded


def _random_wechat_uin() -> str:
    """Generate X-WECHAT-UIN: random uint32 → decimal string → base64."""
    val = random.randint(0, 0xFFFFFFFF)
    return base64.b64encode(str(val).encode()).decode()


class WeChatChannel(Channel):
    """WeChat iLink Bot protocol — QR login + getupdates long-poll."""

    name = "wechat"
    ILINK_BASE = "https://ilinkai.weixin.qq.com"
    ILINK_CDN = "https://novac2c.cdn.weixin.qq.com/c2c"

    def __init__(self, state_dir: Path) -> None:
        import httpx

        self._httpx = httpx
        self._state_dir = state_dir
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._cred_path = state_dir / "wechat_session.json"

        self._http = httpx.Client(timeout=40.0)
        self._bot_token: str = ""
        self._base_url: str = self.ILINK_BASE
        self._account_id: str = ""
        self._user_id: str = ""
        self._get_updates_buf: str = ""
        self._closing = False

        self._context_tokens: dict[str, str] = {}

        self._load_creds()

    # -- credential persistence --

    def _load_creds(self) -> None:
        if not self._cred_path.exists():
            return
        try:
            data = json.loads(self._cred_path.read_text("utf-8"))
            self._bot_token = data.get("token", "")
            self._base_url = data.get("baseUrl", self.ILINK_BASE)
            self._account_id = data.get("accountId", "")
            self._user_id = data.get("userId", "")
            self._get_updates_buf = data.get("get_updates_buf", "")
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            log.debug("wechat: failed to load credentials: %s", exc)

    def _save_creds(self) -> None:
        data = {
            "token": self._bot_token,
            "baseUrl": self._base_url,
            "accountId": self._account_id,
            "userId": self._user_id,
            "get_updates_buf": self._get_updates_buf,
            "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._cred_path.write_text(json.dumps(data, indent=2), "utf-8")

    def _clear_creds(self) -> None:
        self._bot_token = ""
        self._get_updates_buf = ""
        if self._cred_path.exists():
            self._cred_path.unlink()

    # -- common headers --

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self._bot_token}",
            "X-WECHAT-UIN": _random_wechat_uin(),
        }

    # -- QR login flow --

    @property
    def is_logged_in(self) -> bool:
        return bool(self._bot_token)

    def login(self) -> bool:
        """Interactive QR-code login.  Returns True on success."""
        print("  [wechat] Requesting QR code...")
        try:
            resp = self._http.get(
                f"{self.ILINK_BASE}/ilink/bot/get_bot_qrcode",
                params={"bot_type": "3"},
                headers={"iLink-App-ClientVersion": "1"},
            )
            data = resp.json()
        except Exception as exc:
            print(f"  [wechat] QR request failed: {exc}")
            return False

        qrcode_id = data.get("qrcode", "")
        qrcode_url = data.get("qrcode_img_content", "")
        if not qrcode_id:
            print(f"  [wechat] Unexpected QR response: {data}")
            return False

        print("  [wechat] Scan QR code with WeChat:")
        print(f"  {qrcode_url}")

        import qrcode as _qr
        qr = _qr.QRCode(error_correction=_qr.constants.ERROR_CORRECT_L, box_size=1, border=1)
        qr.add_data(qrcode_url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)

        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                resp = self._http.get(
                    f"{self.ILINK_BASE}/ilink/bot/get_qrcode_status",
                    params={"qrcode": qrcode_id},
                    headers={"iLink-App-ClientVersion": "1"},
                    timeout=40.0,
                )
                status_data = resp.json()
            except self._httpx.TimeoutException:
                continue
            except Exception as exc:
                print(f"  [wechat] QR poll error: {exc}")
                return False

            status = status_data.get("status", "wait")
            if status == "wait":
                continue
            if status == "scaned":
                print("  [wechat] QR scanned, waiting for confirmation...")
                continue
            if status == "expired":
                print("  [wechat] QR code expired.")
                return False
            if status == "confirmed":
                self._bot_token = status_data.get("bot_token", "")
                self._base_url = status_data.get("baseurl", self.ILINK_BASE)
                self._account_id = status_data.get("ilink_bot_id", "")
                self._user_id = status_data.get("ilink_user_id", "")
                self._get_updates_buf = ""
                self._save_creds()
                print(f"  [wechat] Login successful (account={self._account_id})")
                return True
            log.warning("wechat QR unknown status: %s", status)

        print("  [wechat] QR login timed out (5 min).")
        return False

    # -- CDN media download --

    def _download_cdn_media(
        self, media: dict, aeskey_fallback: str = "", timeout: float = 30.0,
    ) -> bytes | None:
        """Download + AES-128-ECB decrypt a CDN media blob.

        Mirrors hermes-agent: tries ``encrypt_query_param`` first, falls back
        to ``full_url``, and only decrypts when an AES key is available.
        """
        eqp = media.get("encrypt_query_param", "")
        full_url = media.get("full_url", "")
        if not eqp and not full_url:
            return None
        # PROFILE
        from pip_agent import _profile

        with _profile.span_sync(
            "wechat.media_download",
            channel="wechat",
            has_eqp=bool(eqp),
            timeout=timeout,
        ):
            try:
                if eqp:
                    resp = self._http.get(
                        f"{self.ILINK_CDN}/download",
                        params={"encrypted_query_param": eqp},
                        timeout=timeout,
                    )
                else:
                    resp = self._http.get(full_url, timeout=timeout)
                resp.raise_for_status()
                data = resp.content
                raw_key = aeskey_fallback or media.get("aes_key", "")
                if raw_key:
                    key = _parse_ilink_aes_key(raw_key)
                    data = _aes_ecb_decrypt(data, key)
                # PROFILE
                _profile.event(
                    "wechat.media_bytes",
                    channel="wechat",
                    bytes=len(data) if data else 0,
                    decrypted=bool(raw_key),
                )
                return data
            except Exception as exc:
                log.warning("wechat CDN download/decrypt failed: %s", exc)
                return None

    def _collect_ilink_item(
        self, item: dict, texts: list[str], atts: list[Attachment],
    ) -> None:
        """Extract text / image / voice / file from a single iLink item."""
        itype = item.get("type")
        if itype == 1:  # TEXT
            t = (item.get("text_item") or {}).get("text", "")
            if t:
                texts.append(t)
        elif itype == 2:  # IMAGE
            img = item.get("image_item") or {}
            media = img.get("media") or {}
            img_data = self._download_cdn_media(media, img.get("aeskey", ""))
            atts.append(Attachment(
                type="image", data=img_data,
                mime_type=_detect_image_mime(img_data) if img_data else "",
                text="" if img_data else "[Image]",
            ))
        elif itype == 3:  # VOICE
            voice = item.get("voice_item") or {}
            asr = voice.get("text", "")
            atts.append(Attachment(
                type="voice",
                text=asr if asr else "[Voice message]",
            ))
        elif itype == 4:  # FILE
            fi = item.get("file_item") or {}
            media = fi.get("media") or {}
            file_data = self._download_cdn_media(media, timeout=60.0)
            fname = fi.get("file_name", "file")
            text_content = ""
            if file_data:
                try:
                    text_content = file_data.decode("utf-8")
                except (UnicodeDecodeError, ValueError):
                    pass
            atts.append(Attachment(
                type="file", data=file_data, filename=fname, text=text_content,
            ))

    # -- getupdates long-poll --

    def poll(self) -> list[InboundMessage]:
        """One round of getupdates.  Returns parsed messages."""
        # PROFILE
        from pip_agent import _profile

        with _profile.span_sync("wechat.poll", channel="wechat"):
            return self._poll_inner()

    def _poll_inner(self) -> list[InboundMessage]:  # PROFILE — split from poll()
        from pip_agent import _profile  # PROFILE

        try:
            with _profile.span_sync("wechat.poll_http", channel="wechat"):  # PROFILE
                resp = self._http.post(
                    f"{self._base_url}/ilink/bot/getupdates",
                    headers=self._headers(),
                    json={
                        "get_updates_buf": self._get_updates_buf,
                        "base_info": {"channel_version": "1.0.0"},
                    },
                    timeout=40.0,
                )
                data = resp.json()
        except Exception as exc:
            if not self._closing:
                log.warning("wechat getupdates error: %s", exc)
            return []

        ret = data.get("ret", 0)
        if ret == -14:
            print("  [wechat] Session expired (-14), need re-login.")
            self._clear_creds()
            return []
        if ret != 0:
            log.warning("wechat getupdates ret=%s: %s", ret, data.get("errmsg", ""))
            return []

        new_buf = data.get("get_updates_buf", "")
        if new_buf:
            self._get_updates_buf = new_buf
            self._save_creds()

        results: list[InboundMessage] = []
        for msg in data.get("msgs", []):
            if msg.get("message_type") != 1:
                continue
            if msg.get("message_state") not in (0, 2):
                continue

            from_user = msg.get("from_user_id", "")
            ctx_token = msg.get("context_token", "")
            if ctx_token and from_user:
                self._context_tokens[from_user] = ctx_token

            # PROFILE
            with _profile.span_sync("wechat.parse_item", channel="wechat"):
                texts: list[str] = []
                atts: list[Attachment] = []
                for item in msg.get("item_list", []):
                    self._collect_ilink_item(item, texts, atts)
                    ref_item = (item.get("ref_msg") or {}).get("message_item")
                    if isinstance(ref_item, dict):
                        self._collect_ilink_item(ref_item, [], atts)

                text = "\n".join(texts)
                if not text and not atts:
                    continue

                # PROFILE
                _profile.event(
                    "wechat.inbound_received",
                    channel="wechat",
                    text_len=len(text),
                    atts=len(atts),
                    sender=from_user,
                )
                results.append(InboundMessage(
                    text=text,
                    sender_id=from_user,
                    channel="wechat",
                    peer_id=from_user,
                    account_id=self._account_id,
                    raw=msg,
                    attachments=atts,
                ))

        return results

    # -- send --

    def has_context_token(self, peer_id: str) -> bool:
        return bool(self._context_tokens.get(peer_id))

    def send(self, to: str, text: str, **kw: Any) -> bool:
        from pip_agent.fileutil import chunk_message

        # PROFILE
        from pip_agent import _profile

        ctx_token = self._context_tokens.get(to, "")
        if not ctx_token:
            print(f"  [wechat] Cannot reply to {to}: no context_token")
            return False

        with _profile.span_sync(
            "wechat.send", channel="wechat", text_len=len(text),
        ):
            ok = True
            for idx, chunk in enumerate(chunk_message(text, "wechat")):
                client_id = f"pip:{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
                body = {
                    "msg": {
                        "from_user_id": "",
                        "to_user_id": to,
                        "client_id": client_id,
                        "message_type": 2,
                        "message_state": 2,
                        "context_token": ctx_token,
                        "item_list": [{"type": 1, "text_item": {"text": chunk}}],
                    },
                    "base_info": {"channel_version": "1.0.0"},
                }
                try:
                    with _profile.span_sync(  # PROFILE
                        "wechat.send_chunk",
                        channel="wechat",
                        idx=idx,
                        bytes=len(chunk.encode("utf-8")),
                    ):
                        resp = self._http.post(
                            f"{self._base_url}/ilink/bot/sendmessage",
                            headers=self._headers(),
                            json=body,
                        )
                    if resp.status_code != 200:
                        ok = False
                except Exception as exc:
                    log.warning("wechat sendmessage error: %s", exc)
                    ok = False
            return ok

    def send_typing(self, to: str) -> None:
        """Send typing indicator via sendtyping API (fire-and-forget)."""
        ctx_token = self._context_tokens.get(to, "")
        if not ctx_token:
            return
        try:
            self._http.post(
                f"{self._base_url}/ilink/bot/sendtyping",
                headers=self._headers(),
                json={
                    "to_user_id": to,
                    "context_token": ctx_token,
                    "base_info": {"channel_version": "1.0.0"},
                },
                timeout=5.0,
            )
        except Exception as exc:
            log.debug("wechat: send_typing failed: %s", exc)

    def close(self) -> None:
        self._closing = True
        self._http.close()


# ---------------------------------------------------------------------------
# Background poll loop
# ---------------------------------------------------------------------------

def wechat_poll_loop(
    wechat: WeChatChannel,
    queue: list[InboundMessage],
    lock: threading.Lock,
    stop: threading.Event,
    pause: threading.Event | None = None,
) -> None:
    """Long-poll loop for WeChat, runs in a daemon thread."""
    print("  [wechat] Polling started")
    consecutive_errors = 0
    while not stop.is_set():
        if pause is not None and pause.is_set():
            stop.wait(0.5)
            continue
        if not wechat.is_logged_in:
            stop.wait(5.0)
            continue
        try:
            msgs = wechat.poll()
            consecutive_errors = 0
            if msgs:
                with lock:
                    queue.extend(msgs)
        except OSError:
            if stop.is_set():
                break
            consecutive_errors += 1
            wait = min(30.0, 2.0 * consecutive_errors)
            log.warning("wechat poll OSError (retry in %.0fs)", wait)
            stop.wait(wait)
        except Exception as exc:
            if stop.is_set():
                break
            consecutive_errors += 1
            wait = min(30.0, 2.0 * consecutive_errors)
            log.warning("wechat poll error: %s (retry in %.0fs)", exc, wait)
            stop.wait(wait)
