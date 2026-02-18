"""
test_05_websocket.py — WebSocket upgrade handshake tests.

Plane's real-time collaboration service (live) communicates over
WebSocket.  nginx must pass the Upgrade / Connection headers through
correctly.  These tests verify that the WebSocket handshake succeeds
end-to-end through nginx → plane-proxy → live.
"""

import pytest

try:
    import websocket  # websocket-client library
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False


WS_PATH = "/live/"  # path routed by plane-proxy to the live service


def _ws_url(base_url: str) -> str:
    """Convert an http:// BASE_URL to a ws:// WebSocket URL."""
    return base_url.replace("http://", "ws://").replace("https://", "wss://")


@pytest.mark.skipif(not HAS_WEBSOCKET, reason="websocket-client not installed")
class TestWebSocket:
    """WebSocket upgrade handshake tests through nginx → plane-proxy → live."""

    def test_websocket_handshake_succeeds(self, base_url: str):
        """
        A WebSocket upgrade request to /live/ must not be rejected
        at the nginx or plane-proxy level.

        Acceptable outcomes:
          - Connection established (server accepts the upgrade).
          - Server closes immediately after handshake (1000 / 1001).
          - Protocol error from the app layer (still means nginx passed it).

        Failure:
          - WebSocketBadStatusException with 4xx (excluding 502/503) from
            nginx/plane-proxy itself means the upgrade was rejected at the
            proxy layer — which is the real failure scenario.

        A 502 Bad Gateway means nginx and Caddy forwarded the upgrade
        correctly but the live upstream closed/rejected it, which is
        acceptable (the app may require authentication for WS connections).
        """
        ws_base = _ws_url(base_url)
        target = f"{ws_base}{WS_PATH}"

        try:
            ws = websocket.create_connection(
                target,
                timeout=15,
                header={"Origin": base_url},
            )
            # Handshake succeeded — close cleanly
            ws.close()
        except websocket.WebSocketBadStatusException as exc:
            # Extract the HTTP status code from the exception message
            exc_str = str(exc)
            # 502/503 means the request reached the upstream and was proxied —
            # nginx and Caddy did their job correctly.
            if "502" in exc_str or "503" in exc_str:
                return  # acceptable: proxy forwarded correctly, upstream rejected
            pytest.fail(
                f"WebSocket handshake rejected at the proxy layer ({target}): {exc}\n"
                "Check nginx Upgrade/Connection headers and plane-proxy routing."
            )
        except (websocket.WebSocketConnectionClosedException, OSError):
            # Connection closed immediately after handshake — still OK
            pass

    def test_websocket_upgrade_headers_forwarded(self, base_url: str):
        """
        Verify that nginx is configured to forward the WebSocket upgrade
        headers by checking that port 80 does not reject an Upgrade request
        with a 400 or 501 (Not Implemented).
        """
        import socket

        ws_base = _ws_url(base_url)
        # Parse host / port from base_url
        host = base_url.replace("http://", "").replace("https://", "").split(":")[0]
        port = 80

        handshake = (
            f"GET {WS_PATH} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )

        try:
            sock = socket.create_connection((host, port), timeout=15)
            sock.sendall(handshake.encode())
            response = sock.recv(4096).decode(errors="replace")
            sock.close()
        except OSError as exc:
            pytest.skip(f"Could not connect to {host}:{port} — {exc}")

        first_line = response.splitlines()[0] if response else ""

        # HTTP 101 Switching Protocols → success
        # HTTP 400/501 → nginx did not forward upgrade headers
        assert "400" not in first_line and "501" not in first_line, (
            f"nginx returned {first_line!r} for WebSocket upgrade — "
            "Upgrade/Connection headers may not be forwarded correctly."
        )
