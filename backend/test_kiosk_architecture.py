import copy
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import Request

import hub_config
import main


def make_request(client_host: str = "100.64.0.10") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/camera/snapshot",
            "raw_path": b"/api/camera/snapshot",
            "query_string": b"",
            "headers": [],
            "client": (client_host, 12345),
            "server": ("hub", 8443),
        }
    )


class HubConfigContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = copy.deepcopy(hub_config.CONFIG)

    def tearDown(self) -> None:
        hub_config.CONFIG.clear()
        hub_config.CONFIG.update(self.original)

    def test_home_is_viewer_and_upstream_is_backend_only(self):
        hub_config.CONFIG["site"] = "home"
        hub_config.CONFIG["camera"] = {
            "mode": "viewer",
            "gardenHubUrl": "https://garden.example.ts.net:8443",
            "publisherHosts": [],
        }

        hub_config.validate_config(require_camera_upstream=True)
        public = hub_config.public_config()

        self.assertEqual(public["camera"], {"mode": "viewer"})
        self.assertNotIn("gardenHubUrl", json.dumps(public))

    def test_garden_cannot_run_in_viewer_mode(self):
        hub_config.CONFIG["site"] = "garden"
        hub_config.CONFIG["camera"] = {"mode": "viewer"}

        with self.assertRaisesRegex(ValueError, "requires HUB_CAMERA_MODE=publisher"):
            hub_config.validate_config()

    def test_camera_upstream_requires_https(self):
        hub_config.CONFIG["site"] = "home"
        hub_config.CONFIG["camera"] = {
            "mode": "viewer",
            "gardenHubUrl": "http://garden.invalid",
        }

        with self.assertRaisesRegex(ValueError, "absolute HTTPS"):
            hub_config.validate_config()


class CameraBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_never_accepts_snapshot_uploads(self):
        with (
            patch.object(main.hub_config, "feature_enabled", return_value=True),
            patch.object(main.hub_config, "camera_mode", return_value="viewer"),
            patch.object(main.hub_config, "site", return_value="home"),
        ):
            response = await main.upload_camera_snapshot(make_request())

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Only the garden kiosk", response.body)

    def test_only_configured_garden_host_can_publish(self):
        with (
            patch.object(main.hub_config, "camera_mode", return_value="publisher"),
            patch.object(main.hub_config, "site", return_value="garden"),
            patch.object(main.hub_config, "camera_publisher_hosts", return_value={"100.64.0.20"}),
        ):
            self.assertTrue(main._camera_publisher_allowed(make_request("100.64.0.20")))
            self.assertFalse(main._camera_publisher_allowed(make_request("100.64.0.21")))

    async def test_home_proxy_marks_status_source(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                str(request.url),
                "https://garden.example.ts.net:8443/api/camera/status",
            )
            return httpx.Response(
                200,
                json={"ok": True, "available": True, "age": 1.25},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with (
                patch.object(main, "_camera_http", client),
                patch.object(
                    main.hub_config,
                    "garden_hub_url",
                    return_value="https://garden.example.ts.net:8443",
                ),
            ):
                result = await main._garden_camera_json("/api/camera/status")

        self.assertIsInstance(result, dict)
        self.assertEqual(result["source"], "garden-proxy")
        self.assertTrue(result["available"])

    async def test_home_proxy_returns_explicit_offline_state_on_timeout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("offline", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with (
                patch.object(main, "_camera_http", client),
                patch.object(
                    main.hub_config,
                    "garden_hub_url",
                    return_value="https://garden.example.ts.net:8443",
                ),
            ):
                response = await main._garden_camera_json("/api/camera/status")

        self.assertEqual(response.status_code, 502)
        payload = json.loads(response.body)
        self.assertFalse(payload["available"])
        self.assertEqual(payload["source"], "garden-proxy")

    async def test_health_reports_release_and_role(self):
        with (
            patch.dict(os.environ, {"HUB_RELEASE": "abc123"}, clear=False),
            patch.object(main.hub_config, "site", return_value="home"),
            patch.object(main.hub_config, "camera_mode", return_value="viewer"),
            patch.object(main.hub_config, "garden_hub_url", return_value="https://garden.example.ts.net"),
        ):
            result = await main.health()

        self.assertEqual(
            result,
            {
                "ok": True,
                "site": "home",
                "cameraMode": "viewer",
                "gardenUpstreamConfigured": True,
                "release": "abc123",
            },
        )


if __name__ == "__main__":
    unittest.main()
