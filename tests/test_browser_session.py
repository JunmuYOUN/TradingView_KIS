import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx
from starlette.responses import PlainTextResponse

from server.config import Settings
from server.guard import LocalGuard
from server import main


async def ok(scope, receive, send):
    await PlainTextResponse("ok")(scope, receive, send)


class BrowserAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_exchange_authorizes_one_browser_only(self):
        guard = LocalGuard(ok, host="127.0.0.1", port=8123)
        base = "http://127.0.0.1:8123"
        transport = httpx.ASGITransport(app=guard)
        url = guard.launch_url(base)
        async with httpx.AsyncClient(transport=transport, base_url=base) as browser:
            self.assertEqual((await browser.get("/api/settings")).status_code, 403)
            exchange = await browser.get(url)
            self.assertEqual(exchange.status_code, 303)
            self.assertEqual(exchange.headers["location"], "/")
            self.assertEqual(exchange.headers["cache-control"], "no-store")
            self.assertEqual(exchange.headers["referrer-policy"], "no-referrer")
            self.assertIn("HttpOnly", exchange.headers["set-cookie"])
            self.assertIn("SameSite=strict", exchange.headers["set-cookie"])
            self.assertEqual((await browser.get("/api/settings")).text, "ok")
            self.assertEqual((await browser.get("/", headers={"Origin": "https://outside.test"})).status_code, 403)
        async with httpx.AsyncClient(transport=transport) as other:
            self.assertEqual((await other.get(url)).status_code, 403)

        messages = []
        async def send(message):
            messages.append(message)
        await guard({"type": "websocket", "scheme": "ws", "path": "/ws/ticks",
                     "headers": [(b"host", b"127.0.0.1:8123")]}, None, send)
        self.assertEqual(messages, [{"type": "websocket.close", "code": 1008}])

    async def test_https_named_host_and_secure_cookie(self):
        guard = LocalGuard(ok, host="0.0.0.0", port=443, public_host="chart.example", https=True)
        base = "https://chart.example"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guard), base_url=base) as browser:
            url = guard.launch_url(base)
            self.assertEqual((await browser.get(url, headers={"Host": "other.example"})).status_code, 400)
            exchange = await browser.get(url)
            self.assertIn("Secure", exchange.headers["set-cookie"])
            self.assertEqual((await browser.get("/", headers={"Origin": base})).text, "ok")


class BrowserLauncherTests(unittest.TestCase):
    settings = Settings("", "", "demo", "J", "mock", "127.0.0.1", 0, 4, False)

    def test_manual_and_automatic_entry_points(self):
        for automatic in (False, True):
            with self.subTest(automatic=automatic), patch.object(main, "load_settings", return_value=self.settings), \
                 patch.dict(os.environ, OPEN_BROWSER=str(int(automatic))), patch("builtins.print") as terminal, \
                 patch("uvicorn.Server.run", autospec=True) as run, patch.object(main.app, "state"):
                main.main()
                instance = run.call_args.args[0]
                listener, = run.call_args.kwargs["sockets"]
                self.assertEqual(listener.fileno(), -1)
                self.assertFalse(instance.config.access_log)
                self.assertFalse(instance.config.proxy_headers)
                base = f"http://127.0.0.1:{instance.config.port}"
                expected = instance.config.app.launch_url(base)
                self.assertTrue(expected.startswith(base + "/?launch="))
                if automatic:
                    terminal.assert_not_called()
                    self.assertEqual(main.app.state.browser_url, expected)
                else:
                    self.assertIn(expected, terminal.call_args.args[0])

    def test_external_binding_requires_tls(self):
        with patch.object(main, "load_settings", return_value=replace(self.settings, host="0.0.0.0")), \
             patch("socket.socket") as listener, self.assertRaisesRegex(SystemExit, "HTTPS"):
            main.main()
        listener.assert_not_called()
