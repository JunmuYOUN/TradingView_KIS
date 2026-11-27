import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI

import desktop_login
from server import config, kis_source, main, yahoo_source


class WindowsGuestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.dict(os.environ, DATA_SOURCE="auto", KIS_APP_KEY="", KIS_APP_SECRET="",
                                      KIS_ENV="demo", SYMBOL_DOWNLOAD="off"))
        stack.enter_context(patch.multiple(config, ENV_FILE=root / ".env", DATA_DIR=root,
                                           CREDENTIALS_FILE=root / "credentials.bin"))
        for module in (main, kis_source):
            stack.enter_context(patch.object(module, "DATA_DIR", root))
        self.save = stack.enter_context(patch.object(desktop_login, "save_stored_connection"))
        self.forget = stack.enter_context(patch.object(desktop_login, "forget_connection"))
        self.verify = stack.enter_context(patch.object(kis_source.KisRest, "verify_session", new_callable=AsyncMock))
        self.yahoo_load = stack.enter_context(patch.object(yahoo_source, "_load_chart", new_callable=AsyncMock))

    async def test_no_key_start_login_logout_keeps_yahoo_provider_available(self):
        app = FastAPI()
        app.state.desktop = True
        app.state.initial_settings = desktop_login.load_startup_settings()
        async with main.lifespan(app):
            state = app.state
            yahoo = state.yahoo
            self.assertEqual(state.settings.source, "yahoo")
            self.assertIs(state.source, yahoo)
            self.assertEqual(await desktop_login.restore_desktop_session(state), "")
            self.verify.assert_not_awaited()

            self.verify.side_effect = kis_source.KisCredentialError("invalid", "invalid test key")
            self.assertTrue(await desktop_login.connect_kis(state, "example", "example"))
            self.assertIs(state.source, yahoo)
            self.save.assert_not_called()

            self.verify.side_effect = None
            self.assertEqual(await desktop_login.connect_kis(state, "example", "example"), "")
            self.assertEqual(state.source.name, "kis")
            self.save.assert_called_once()

            # Explicit Yahoo instruments remain usable while KIS is selected.
            self.yahoo_load.return_value = []
            self.assertEqual(await yahoo.recent_bars("^VIX", "1"), [])
            self.yahoo_load.assert_awaited_once()

            self.assertEqual(await desktop_login.logout_kis(state), "")
            self.assertIs(state.source, yahoo)
            self.assertEqual((state.settings.source, state.settings.app_key, state.settings.app_secret),
                             ("yahoo", "", ""))
            self.forget.assert_called_once()
            self.assertEqual(await state.source.recent_bars("^GSPC", "1"), [])
            self.assertEqual(self.yahoo_load.await_count, 2)

    async def test_saved_connection_restores_from_windows_guest(self):
        saved = {"KIS_APP_KEY": "example", "KIS_APP_SECRET": "example", "KIS_ENV": "demo"}
        app = FastAPI()
        app.state.desktop = True
        with patch.object(desktop_login, "load_stored_connection", return_value=saved):
            app.state.initial_settings = desktop_login.load_startup_settings()
        async with main.lifespan(app):
            self.assertEqual(app.state.source.name, "yahoo")
            self.assertIsNone(await desktop_login.restore_desktop_session(app.state))
            self.verify.assert_awaited_once()
            self.assertEqual(app.state.settings.source, "kis")
            self.save.assert_not_called()

    async def test_other_entry_points_keep_keyless_mock_mode(self):
        for entrypoint in ("browser", "android"):
            with self.subTest(entrypoint=entrypoint):
                app = FastAPI()
                if entrypoint == "android":
                    app.state.desktop = False
                    app.state.initial_settings = desktop_login.load_desktop_settings()
                async with main.lifespan(app):
                    self.assertEqual(app.state.source.name, "mock")
                    self.assertIsNot(app.state.source, app.state.yahoo)
                    if entrypoint == "android":
                        self.assertEqual(await desktop_login.logout_kis(app.state), "")
                        self.assertEqual(app.state.source.name, "mock")
