import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

import main


def completion(payload, finish_reason="stop", refusal=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(payload) if isinstance(payload, dict) else payload, refusal=refusal),
        finish_reason=finish_reason,
    )])


def fake_provider(payload):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=completion(payload)))),
        close=AsyncMock(),
    )


class APITests(unittest.TestCase):
    def setUp(self):
        self.payload = copy.deepcopy(main.FALLBACK_SPATIAL_LAYOUTS["office"])
        self.provider = fake_provider(self.payload)
        self.settings = main.Settings(openai_api_key=SecretStr("test-key-not-real"), generation_limit_per_minute=60, generation_limit_per_hour=1000)
        factory = patch.object(main, "AsyncOpenAI", return_value=self.provider)
        factory.start()
        self.addCleanup(factory.stop)
        self.client = TestClient(main.create_app(self.settings), base_url="http://localhost", raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def generate(self, **kwargs):
        return self.client.post("/generate", json={"text": "An office with a desk"}, **kwargs)

    def test_structured_output_and_exact_prompt(self):
        prompt = "  A library with reading desks\n"
        response = self.client.post("/generate", json={"text": prompt})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "openai")
        options = self.provider.chat.completions.create.call_args.kwargs
        self.assertEqual(options["messages"][-1]["content"], prompt)
        self.assertEqual(options["model"], "gpt-4.1-mini")
        self.assertTrue(options["response_format"]["json_schema"]["strict"])
        self.assertFalse(options["response_format"]["json_schema"]["schema"]["additionalProperties"])

    def test_legacy_route(self):
        self.assertEqual(self.client.post("/generate_3d_room", json={"text": "office"}).status_code, 200)

    def test_markdown_fences(self):
        for fence in ["```json", "```JSON", "```"]:
            with self.subTest(fence=fence):
                self.provider.chat.completions.create.return_value = completion(fence + "\n" + json.dumps(self.payload) + "\n```")
                self.assertEqual(self.generate().status_code, 200)

    def test_invalid_json_is_not_a_demo_or_raw_response(self):
        self.provider.chat.completions.create.return_value = completion("private invalid content")
        response = self.generate()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private invalid content", response.text)
        self.assertNotIn("raw", response.json())

    def test_invalid_geometry_and_types(self):
        for field, value in [("w", 0), ("w", True), ("h", 4), ("x", 6), ("y", -1), ("z", float("nan")), ("color", "red")]:
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(self.payload)
                payload["layout"][0][field] = value
                self.provider.chat.completions.create.return_value = completion(payload)
                self.assertEqual(self.generate().status_code, 502)

    def test_object_and_cloth_limits(self):
        for count, name in [(25, "Chair"), (3, "Queen Bed,")]:
            with self.subTest(count=count):
                payload = copy.deepcopy(self.payload)
                item = {**payload["layout"][0], "name": name}
                payload["layout"] = [item] * count
                self.provider.chat.completions.create.return_value = completion(payload)
                self.assertEqual(self.generate().status_code, 502)

    def test_invalid_inputs_do_not_call_openai(self):
        for body in [{"text": " "}, {"text": "x" * 4001}, {"text": 4}, {"prompt": "office"}, {"text": "office", "extra": True}]:
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/generate", json=body).status_code, 422)
        self.provider.chat.completions.create.assert_not_awaited()

    def test_content_type_and_body_limits(self):
        self.assertEqual(self.client.post("/generate", content="text", headers={"Content-Type": "text/plain"}).status_code, 415)
        self.assertEqual(self.client.post("/generate", content="x" * 17000, headers={"Content-Type": "application/json"}).status_code, 413)
        self.assertEqual(self.client.post("/generate", content="{bad", headers={"Content-Type": "application/json"}).status_code, 422)
        self.provider.chat.completions.create.assert_not_awaited()

    def test_origin_and_host_checks(self):
        self.assertEqual(self.generate(headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.generate(headers={"Host": "other.example"}).status_code, 400)
        self.provider.chat.completions.create.assert_not_awaited()

    def test_authentication(self):
        settings = self.settings.model_copy(update={"app_password": SecretStr("test-password-not-real")})
        with TestClient(main.create_app(settings), base_url="http://localhost") as client:
            self.assertEqual(client.get("/").status_code, 401)
            self.assertEqual(client.post("/generate", json={"text": "office"}).status_code, 401)
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.post("/generate", json={"text": "office"}, auth=("admin", "incorrect")).status_code, 401)
            self.assertEqual(client.post("/generate", json={"text": "office"}, auth=("admin", "test-password-not-real")).status_code, 200)

    def test_production_configuration_and_docs(self):
        settings = self.settings.model_copy(update={"app_env": "production", "app_password": SecretStr("test-password-not-real"), "public_origin": "https://omnispace.example", "allowed_hosts": ["omnispace.example"]})
        with TestClient(main.create_app(settings), base_url="https://omnispace.example") as client:
            self.assertEqual(client.get("/docs").status_code, 404)
            self.assertEqual(client.get("/openapi.json").status_code, 404)
            response = client.post("/generate", json={"text": "office"}, auth=("admin", "test-password-not-real"), headers={"Origin": "https://omnispace.example"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("strict-transport-security", response.headers)
        for changes in [{"openai_api_key": None}, {"demo_mode": True}, {"app_password": None}, {"app_password": SecretStr("short")}, {"public_origin": "http://omnispace.example"}, {"allowed_hosts": ["*"]}, {"allowed_hosts": ["omnispace.example", "*.example"]}]:
            with self.subTest(changes=list(changes)):
                with self.assertRaises(RuntimeError):
                    settings.model_copy(update=changes).check_production()

    def test_rate_limits(self):
        for changes in [{"generation_limit_per_minute": 1}, {"generation_limit_per_hour": 1}]:
            with self.subTest(changes=changes):
                with TestClient(main.create_app(self.settings.model_copy(update=changes)), base_url="http://localhost") as client:
                    self.assertEqual(client.post("/generate", json={"text": "office"}).status_code, 200)
                    limited = client.post("/generate", json={"text": "office"})
                    self.assertEqual(limited.status_code, 429)
                    self.assertGreaterEqual(int(limited.headers["Retry-After"]), 59)

    def test_health_readiness_and_missing_key(self):
        with TestClient(main.create_app(main.Settings()), base_url="http://localhost") as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.get("/readyz").status_code, 503)
            self.assertEqual(client.post("/generate", json={"text": "office"}).status_code, 503)

    def test_demo_is_explicit_and_never_uses_openai(self):
        with TestClient(main.create_app(main.Settings(demo_mode=True)), base_url="http://localhost") as client:
            for kind in main.FALLBACK_SPATIAL_LAYOUTS:
                response = client.post("/generate", json={"text": kind})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["source"], "demo")
            self.assertEqual(client.get("/readyz").json()["mode"], "demo")
        self.provider.chat.completions.create.assert_not_awaited()

    def test_security_headers_and_no_key_in_html(self):
        response = self.client.get("/")
        for header in ["x-request-id", "x-content-type-options", "x-frame-options", "content-security-policy", "cache-control"]:
            self.assertIn(header, response.headers)
        self.assertIn("style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net", response.headers["content-security-policy"])
        self.assertIn("frame-src https://www.youtube-nocookie.com", response.headers["content-security-policy"])
        self.assertIn('autoplay=(self "https://www.youtube-nocookie.com")', response.headers["permissions-policy"])
        self.assertNotIn("test-key-not-real", response.text)

    def test_provider_errors_are_sanitized(self):
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        cases = [
            (main.AuthenticationError("private payload", response=httpx.Response(401, request=request), body=None), 503),
            (main.PermissionDeniedError("private payload", response=httpx.Response(403, request=request), body=None), 503),
            (main.RateLimitError("private payload", response=httpx.Response(429, request=request), body=None), 429),
            (main.APITimeoutError(request=request), 504),
            (main.APIConnectionError(request=request), 502),
            (main.APIStatusError("private payload", response=httpx.Response(500, request=request), body=None), 502),
            (RuntimeError("private payload"), 500),
        ]
        for error, status in cases:
            with self.subTest(error=type(error).__name__):
                self.provider.chat.completions.create.side_effect = error
                response = self.generate()
                self.assertEqual(response.status_code, status, response.text)
                self.assertNotIn("private payload", response.text)
                self.assertIn("x-request-id", response.headers)

    def test_refusal_and_incomplete_output(self):
        self.provider.chat.completions.create.return_value = completion(None, refusal="Not supported")
        self.assertEqual(self.generate().status_code, 422)
        self.provider.chat.completions.create.return_value = completion(self.payload, finish_reason="length")
        self.assertEqual(self.generate().status_code, 502)
        self.provider.chat.completions.create.return_value = SimpleNamespace(choices=[])
        self.assertEqual(self.generate().status_code, 502)

    def test_total_deadline(self):
        async def timed_out(awaitable, timeout):
            self.assertEqual(timeout, self.settings.openai_timeout_seconds)
            awaitable.close()
            raise TimeoutError()

        with patch.object(main.asyncio, "wait_for", new=timed_out):
            self.assertEqual(self.generate().status_code, 504)


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_does_not_block_event_loop_and_capacity_is_bounded(self):
        payload = copy.deepcopy(main.FALLBACK_SPATIAL_LAYOUTS["office"])
        provider = fake_provider(payload)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def respond(**kwargs):
            entered.set()
            await release.wait()
            return completion(payload)

        provider.chat.completions.create.side_effect = respond
        settings = main.Settings(openai_api_key=SecretStr("test-key-not-real"), max_concurrent_generations=1)
        with patch.object(main, "AsyncOpenAI", return_value=provider):
            app = main.create_app(settings)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                    first = asyncio.create_task(client.post("/generate", json={"text": "office"}))
                    try:
                        await asyncio.wait_for(entered.wait(), 2)
                        self.assertEqual((await client.get("/healthz")).status_code, 200)
                        second = await client.post("/generate", json={"text": "office"})
                        self.assertEqual(second.status_code, 503)
                    finally:
                        release.set()
                        response = await first
                    self.assertEqual(response.status_code, 200)
            provider.close.assert_awaited_once()


class LayoutArrangementTests(unittest.TestCase):
    def room(self, objects):
        return main.RoomLayout.model_validate({"layout": objects, "design_recommendation": "Keep paths clear.", "sustainability_insight": "Use durable materials."})

    def item(self, name, width=1, height=1, depth=1, x=0, z=0, y=None):
        return {"name": name, "w": width, "h": height, "d": depth, "x": x, "y": height / 2 if y is None else y, "z": z, "color": "#738377"}

    def test_overlapping_floor_furniture_is_separated(self):
        room = self.room([self.item("Sofa", 3, 0.9, 1.2), self.item("Coffee Table", 1.4, 0.5, 0.8), self.item("Armchair", 0.9, 1, 0.9), self.item("Floor Lamp", 0.4, 2, 0.4)])
        arranged = main.arrange_layout(room)
        for index, first in enumerate(arranged.layout):
            for second in arranged.layout[index + 1:]:
                self.assertFalse(main.rectangles_overlap(main.layout_rect(first), main.layout_rect(second), 0.1))
        self.assertEqual(room.layout[1].x, 0)
        self.assertEqual(room.layout[1].z, 0)

    def test_tv_uses_console_and_sofa_faces_it(self):
        room = self.room([self.item("Media Console", 2.6, 0.5, 0.6, z=-3.5), self.item("OLED TV", 1.6, 0.9, 0.08, z=-3.5, y=1.5), self.item("Sofa", 3, 0.9, 1.2, z=2)])
        arranged = main.arrange_layout(room)
        console, tv, sofa = arranged.layout
        self.assertAlmostEqual(tv.y, console.y + console.h / 2 + tv.h * 0.5925 + 0.015)
        self.assertEqual(sofa.rotation, -180)
        self.assertEqual(tv.rotation, 0)

    def test_monitor_is_supported_and_chair_faces_desk(self):
        room = self.room([self.item("Desk", 2, 0.8, 1), self.item("Monitor", 1.2, 0.6, 0.12, y=2), self.item("Desk Chair", 0.7, 1, 0.7, z=1.5)])
        arranged = main.arrange_layout(room)
        desk, monitor, chair = arranged.layout
        self.assertGreater(monitor.y - monitor.h * 0.5925, desk.h)
        self.assertEqual(chair.rotation, -180)

    def test_headboard_and_rug_are_intentional_layers(self):
        room = self.room([self.item("Queen Bed", 2, 0.9, 2.2, z=-2.5), self.item("Headboard", 2.2, 1.6, 0.2, z=-3.5), self.item("Area Rug", 4, 0.04, 4, z=-1)])
        arranged = main.arrange_layout(room)
        bed, head, rug = arranged.layout
        self.assertFalse(main.rectangles_overlap(main.layout_rect(bed), main.layout_rect(head), 0))
        self.assertAlmostEqual(head.x, bed.x)
        self.assertAlmostEqual(rug.y, rug.h / 2)

    def test_rotation_is_backward_compatible_and_required_for_ai(self):
        item = main.LayoutObject.model_validate(self.item("Chair"))
        self.assertEqual(item.rotation, 0)
        schema = main.structured_layout_schema()["$defs"]["LayoutObject"]
        self.assertIn("rotation", schema["required"])
        self.assertNotIn("default", schema["properties"]["rotation"])

    def test_all_demo_layouts_arrange_with_clear_doors(self):
        for name, payload in main.FALLBACK_SPATIAL_LAYOUTS.items():
            with self.subTest(room=name):
                arranged = main.arrange_layout(main.RoomLayout.model_validate(payload))
                self.assertEqual(len(arranged.layout), len(payload["layout"]))
                for item in arranged.layout:
                    main.LayoutObject.model_validate(item.model_dump())

    def test_model_positions_are_repaired_before_final_validation(self):
        payload = {"layout": [self.item("Desk", 2, 0.8, 1, x=5.2, y=2.5)], "design_recommendation": "Keep paths clear.", "sustainability_insight": "Use durable materials."}
        raw = main.parse_layout(json.dumps(payload))
        arranged = main.arrange_layout(raw)
        self.assertLessEqual(abs(arranged.layout[0].x) + arranged.layout[0].w / 2, 5.15)
        self.assertEqual(arranged.layout[0].y, 0.4)
        main.LayoutObject.model_validate(arranged.layout[0].model_dump())

    def test_orphan_tv_is_wall_mounted_and_orphan_lamp_is_grounded(self):
        room = self.room([self.item("Television", 1.6, 0.9, 0.08), self.item("Desk Lamp", 0.3, 0.6, 0.3)])
        television, lamp = main.arrange_layout(room).layout
        self.assertTrue(television.name.startswith("Wall-mounted"))
        self.assertGreater(television.y, television.h / 2)
        self.assertAlmostEqual(lamp.y, lamp.h / 2)

    def test_doors_have_clear_floor_approaches(self):
        room = self.room([self.item("Door", 0.9, 2.1, 0.1, z=-4.8), self.item("Desk", 2, 0.8, 1, z=-4), self.item("Chair", 0.7, 1, 0.7, z=-3.5)])
        door, *furniture = main.arrange_layout(room).layout
        approach = (door.x - door.w / 2 - 0.15, -5.25, door.x + door.w / 2 + 0.15, -3.9)
        for item in furniture:
            self.assertFalse(main.rectangles_overlap(approach, main.layout_rect(item), 0.1))

    def test_barstools_face_the_island(self):
        room = self.room([self.item("Kitchen Island", 2.8, 0.92, 1.3), self.item("Barstool", 0.5, 0.95, 0.5, z=1.4)])
        island, stool = main.arrange_layout(room).layout
        self.assertEqual(stool.rotation, -180)
        self.assertFalse(main.rectangles_overlap(main.layout_rect(island), main.layout_rect(stool), 0.1))


if __name__ == "__main__":
    unittest.main()