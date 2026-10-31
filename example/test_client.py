"""Run with: uv run python -m unittest -v."""

import asyncio
import copy
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import client


DATA = {"time_unit": "min", "time": [0, 0.1, 0.2],
        "channels": ["mz2"], "intensity": [[-1e-12], [2e-12], [3e-12]]}
HELLO = {"version": 1, "type": "hello", "device": {"id": "test", "model": None,
         "os": {"name": "linux", "distribution": None, "version": None,
                "kernel": None, "arch": "arm64"}},
         "cpu": {"model": None, "logical_cores": 4}, "memory": {"total_bytes": None},
         "functions": {"denoise": ["none", "mean"]}}


def heartbeat(seq=1):
    return {"version": 1, "type": "heartbeat", "connection_id": "test-connection",
            "seq": seq, "timestamp": "2026-10-26T00:00:00Z", "sample_age_ms": 0,
            "uptime_sec": 1, "device": HELLO["device"],
            "cpu": {**HELLO["cpu"], "usage_percent": None, "sample_window_ms": None},
            "memory": {"total_bytes": None, "available_bytes": None,
                       "used_bytes": None, "usage_percent": None},
            "service": {"state": "processing", "active_requests": 1},
            "collection_errors": []}


def result(request):
    return {"version": 1, "type": "result", "request_id": request["request_id"],
            "status": "ok", "algorithm_version": "1.0.0", "data": copy.deepcopy(DATA),
            "stats": {"total": 1, "processed": 1, "skipped": 0, "errors": 0},
            "column_results": [{"channel": "mz2", "status": "processed",
                                "requested_method": "none", "applied_method": "none",
                                "effective_params": {}, "code": "PROCESSED"}],
            "timing": {"processing_ms": 1}}


class CSVTests(unittest.TestCase):
    def test_bom_negative_values_and_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.csv"
            source.write_text("\ufeffTime/min,mz2\n0,-1e-12\n0.1,2e-12\n0.2,3e-12\n", encoding="utf-8")
            self.assertEqual(client.read_csv(source), DATA)
            target = Path(directory) / "nested" / "result.csv"
            client.save_csv(target, DATA)
            with target.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0], ["Time/min", "mz2"])
            self.assertEqual(float(rows[1][1]), -1e-12)
            self.assertEqual(client.read_csv(target), DATA)

    def test_invalid_csv_is_rejected(self):
        cases = ["time,mz2\n0,1\n", "Time/min,mz2,mz2\n0,1,2\n",
                 "Time/min,mz2\n0,NaN\n", "Time/min,mz2\n0,1\n0,2\n",
                 "Time/min,mz2\n0,1,2\n", "Time/min,mz2\n",
                 "Time/min,mz2\n0,1e-999\n"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            for contents in cases:
                with self.subTest(contents=contents):
                    path.write_text(contents, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        client.read_csv(path)

    def test_params_must_be_strict_json_object(self):
        self.assertEqual(client.parse_params('{"window_size":3}'), {"window_size": 3})
        for raw in ['null', '[]', '{"x":NaN}', '{"x":1,"x":2}', '{"x":1e999}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                client.parse_params(raw)


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, handler, **kwargs):
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await client.denoise(f"ws://127.0.0.1:{port}/vta", DATA,
                                        "none", {}, log=io.StringIO(), **kwargs)

    async def test_hello_heartbeat_and_matched_result(self):
        async def handler(ws):
            await ws.send(json.dumps(HELLO))
            request = json.loads(await ws.recv())
            self.assertEqual(request["data"], DATA)
            self.assertEqual(request["config"], {"method": "none", "params": {}})
            self.assertEqual(request["type"], "denoise")
            await ws.send(json.dumps(heartbeat()))
            await ws.send(json.dumps(result(request)))
        response = await self.exercise(handler)
        self.assertEqual(response["data"], DATA)

    async def test_mismatched_id_or_changed_axes_are_rejected(self):
        for change in (lambda r: r.update(request_id="another-request"),
                       lambda r: r["data"].update(time=[0, 1, 2]),
                       lambda r: r["data"].update(intensity=[[1], [2]])):
            async def handler(ws):
                await ws.send(json.dumps(HELLO))
                response = result(json.loads(await ws.recv()))
                change(response)
                await ws.send(json.dumps(response))
            with self.assertRaises(client.ClientError):
                await self.exercise(handler)

    async def test_server_error_is_not_a_result(self):
        async def handler(ws):
            await ws.send(json.dumps(HELLO))
            request = json.loads(await ws.recv())
            await ws.send(json.dumps({"version": 1, "type": "error",
                "request_id": request["request_id"],
                "error": {"code": "BUSY", "message": "board busy"}}))
        with self.assertRaisesRegex(client.ClientError, "BUSY"):
            await self.exercise(handler)

    async def test_heartbeats_do_not_extend_request_deadline(self):
        async def handler(ws):
            await ws.send(json.dumps(HELLO))
            await ws.recv()
            try:
                for seq in range(1, 200):
                    await ws.send(json.dumps(heartbeat(seq)))
                    await asyncio.sleep(0.01)
            except ConnectionClosed:
                pass
        with self.assertRaisesRegex(client.ClientError, "结果未知"):
            await asyncio.wait_for(self.exercise(handler, timeout=0.06), timeout=1)

    async def test_repeat_waits_for_result_and_uses_new_ids(self):
        async def handler(ws):
            await ws.send(json.dumps(HELLO))
            ids = set()
            for _ in range(3):
                request = json.loads(await ws.recv())
                self.assertNotIn(request["request_id"], ids)
                ids.add(request["request_id"])
                # 在返回结果之前，客户端不得预发下一张。
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(ws.recv(), 0.02)
                await ws.send(json.dumps(result(request)))
        saved = []
        await self.exercise(handler, repeat=3, on_result=lambda index, r: saved.append((index, r["status"])))
        self.assertEqual(saved, [(1, "ok"), (2, "ok"), (3, "ok")])

    async def test_partial_result_is_delivered_with_diagnostics(self):
        async def handler(ws):
            await ws.send(json.dumps(HELLO))
            response = result(json.loads(await ws.recv()))
            response.update(status="partial", stats={"total": 1, "processed": 0, "skipped": 0, "errors": 1})
            response["column_results"][0].update(status="error", code="NON_FINITE_RESULT")
            await ws.send(json.dumps(response))
        response = await self.exercise(handler)
        self.assertEqual(response["status"], "partial")
        self.assertEqual(response["column_results"][0]["code"], "NON_FINITE_RESULT")

    async def test_disconnect_after_send_is_unknown(self):
        async def handler(ws):
            await ws.send(json.dumps(HELLO))
            await ws.recv()
            await ws.close(code=1001)
        with self.assertRaisesRegex(client.ClientError, "结果未知"):
            await self.exercise(handler)


class CLITests(unittest.IsolatedAsyncioTestCase):
    async def run_cli(self, handler, directory, *extra):
        source = Path(directory) / "input.csv"
        source.write_text("Time/min,mz2\n0,-1e-12\n0.1,2e-12\n0.2,3e-12\n", encoding="utf-8")
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).with_name("client.py")),
                "--url", f"ws://127.0.0.1:{port}/vta", "--input", str(source),
                "--output", str(Path(directory) / "output.csv"), "--method", "none", *extra,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            return process.returncode, stdout.decode(), stderr.decode()

    async def test_partial_saves_csv_and_json_and_exits_two(self):
        async def handler(ws):
            await ws.send(json.dumps(HELLO))
            response = result(json.loads(await ws.recv()))
            response["status"] = "partial"
            response["column_results"][0].update(status="error", code="NON_FINITE_RESULT")
            response["stats"].update(processed=0, errors=1)
            await ws.send(json.dumps(response))
        with tempfile.TemporaryDirectory() as directory:
            code, _, stderr = await self.run_cli(handler, directory, "--save-json")
            self.assertEqual(code, 2, stderr)
            self.assertEqual(client.read_csv(Path(directory) / "output.csv"), DATA)
            response = json.loads((Path(directory) / "output.json").read_text())
            self.assertEqual(response["status"], "partial")
            self.assertIn("NON_FINITE_RESULT", stderr)

    async def test_server_error_preserves_previous_output(self):
        async def handler(ws):
            await ws.send(json.dumps(HELLO))
            request = json.loads(await ws.recv())
            await ws.send(json.dumps({"version": 1, "type": "error",
                "request_id": request["request_id"],
                "error": {"code": "INVALID_CONFIG", "message": "invalid parameter"}}))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.csv"
            output.write_text("previous result")
            code, _, stderr = await self.run_cli(handler, directory, "--save-json")
            self.assertEqual(code, 1, stderr)
            self.assertEqual(output.read_text(), "previous result")
            self.assertFalse(output.with_suffix(".json").exists())


if __name__ == "__main__":
    unittest.main()
