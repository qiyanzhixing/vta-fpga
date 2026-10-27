"""完整 CSV → 单算法降噪 → CSV。依赖由相邻的 pyproject.toml / uv.lock 管理。"""

import argparse
import asyncio
import csv
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Callable, TextIO
import uuid

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException


ROOT = Path(__file__).resolve().parent
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class ClientError(ValueError):
    """连接、协议或请求失败，不能当作有效结果保存。"""


def finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or (value == 0 and Decimal(text) != 0):
        raise ValueError(f"数值超出 float64 范围：{text}")
    return value


def unique_object(pairs: list) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"JSON 字段重复：{key}")
        obj[key] = value
    return obj


def reject_constant(value: str):
    raise ValueError(f"JSON 不允许 {value}")


def parse_json(raw: str) -> dict:
    value = json.loads(raw, object_pairs_hook=unique_object,
                       parse_float=finite_float, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("JSON 必须是对象")
    return value


def parse_params(raw: str) -> dict:
    """保留 JSON 整数类型，具体算法参数约束交由服务端校验。"""
    return parse_json(raw)


def validate_data(data: dict) -> None:
    if not isinstance(data, dict) or data.get("time_unit") != "min":
        raise ValueError("data.time_unit 必须是 min")
    times, channels, intensity = (data.get(k) for k in ("time", "channels", "intensity"))
    if not isinstance(times, list) or not 1 <= len(times) <= 100_000:
        raise ValueError("时间点数量必须在 1～100000 之间")
    if not isinstance(channels, list) or not 1 <= len(channels) <= 1024:
        raise ValueError("通道数量必须在 1～1024 之间")
    if any(not isinstance(c, str) or not c or c == "Time/min" or
           len(c.encode("utf-8")) > 64 for c in channels) or len(set(channels)) != len(channels):
        raise ValueError("通道名必须非空、唯一、最多 64 UTF-8 字节且不能是 Time/min")
    if len(times) * len(channels) > 500_000:
        raise ValueError("强度矩阵最多包含 500000 个数值")
    def number(value):
        return type(value) in (int, float) and math.isfinite(value)
    if any(not number(t) or t < 0 for t in times) or any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("时间必须有限、非负且严格递增")
    if not isinstance(intensity, list) or len(intensity) != len(times):
        raise ValueError("强度矩阵行数与时间点数量不一致")
    if any(not isinstance(row, list) or len(row) != len(channels) or
           any(not number(v) for v in row) for row in intensity):
        raise ValueError("强度矩阵列数不一致或包含非有限数值")


def read_csv(path: Path) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        header = next(reader, [])
        if not header or header[0] != "Time/min" or len(header) < 2:
            raise ValueError("CSV 首列必须为 Time/min，后续列为通道名")
        data = {"time_unit": "min", "time": [], "channels": header[1:], "intensity": []}
        for line, row in enumerate(reader, 2):
            if len(row) != len(header):
                raise ValueError(f"CSV 第 {line} 行列数不一致")
            try:
                values = [finite_float(cell) for cell in row]
            except ValueError as exc:
                raise ValueError(f"CSV 第 {line} 行：{exc}") from exc
            data["time"].append(values[0])
            data["intensity"].append(values[1:])
            if len(data["time"]) > 100_000 or len(data["time"]) * (len(header) - 1) > 500_000:
                raise ValueError("CSV 超出服务端默认数据规模限制")
    validate_data(data)
    return data


def atomic_write(path: Path, writer: Callable[[TextIO], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            writer(stream)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_csv(path: Path, data: dict) -> None:
    validate_data(data)
    def write(stream):
        writer = csv.writer(stream)
        writer.writerow(["Time/min", *data["channels"]])
        writer.writerows([time, *row] for time, row in zip(data["time"], data["intensity"]))
    atomic_write(path, write)


async def receive(ws) -> dict:
    raw = await ws.recv()
    if not isinstance(raw, str):
        raise ClientError("服务端发送了二进制消息，协议要求 JSON 文本")
    try:
        message = parse_json(raw)
    except ValueError as exc:
        raise ClientError(f"服务端 JSON 无效：{exc}") from exc
    if type(message.get("version")) is not int or message["version"] != 1:
        raise ClientError("服务端协议版本必须是 1")
    return message


def validate_result(message: dict, data: dict) -> None:
    try:
        validate_data(message.get("data"))
        if any(message["data"][k] != data[k] for k in ("time", "channels", "time_unit")):
            raise ValueError("返回的时间轴或通道顺序与输入不一致")
        if message.get("status") not in ("ok", "partial"):
            raise ValueError("result.status 无效")
        columns = message.get("column_results")
        if not isinstance(columns, list) or len(columns) != len(data["channels"]) or any(
            not isinstance(c, dict) or c.get("channel") != name or
            c.get("status") not in ("processed", "skipped", "error")
            for c, name in zip(columns, data["channels"])
        ):
            raise ValueError("column_results 与输入通道不对应")
        if not isinstance(message.get("stats"), dict) or not isinstance(message.get("timing"), dict):
            raise ValueError("result 缺少 stats 或 timing")
    except (ValueError, OverflowError) as exc:
        raise ClientError(f"结果校验失败：{exc}") from exc


async def denoise(url: str, data: dict, method: str, params: dict, *, timeout: float = 150,
                  repeat: int = 1, log: TextIO = sys.stderr,
                  on_result: Callable[[int, dict], None] | None = None) -> dict:
    """同一连接串行处理；每次使用新 request_id，绝不自动重放请求。"""
    validate_data(data)
    if repeat < 1 or not math.isfinite(timeout) or timeout <= 0 or not isinstance(params, dict):
        raise ValueError("repeat、timeout 必须为正值，params 必须是对象")
    request = {"version": 1, "type": "denoise", "request_id": str(uuid.uuid4()),
               "data": data, "config": {"method": method, "params": params}}
    payload = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("请求超过服务端默认 16 MiB 限制")
    pending = False
    try:
        async with connect(url, open_timeout=5, close_timeout=3, max_size=MAX_RESPONSE_BYTES,
                           max_queue=4, compression=None, proxy=None,
                           ping_interval=20, ping_timeout=60) as ws:
            async with asyncio.timeout(15):
                hello = await receive(ws)
            functions = hello.get("functions")
            if hello.get("type") != "hello" or not isinstance(functions, dict):
                raise ClientError("建联后的第一条消息必须是包含 functions 的 hello")
            print("hello " + json.dumps(hello, ensure_ascii=False), file=log, flush=True)
            methods = functions.get("denoise")
            if not isinstance(methods, list) or method not in methods:
                raise ClientError(f"设备未声明支持算法 {method!r}；可用：{methods}")
            connection_id, last_seq = None, 0
            for index in range(1, repeat + 1):
                if index > 1:
                    request["request_id"] = str(uuid.uuid4())
                    payload = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                print(f"发送 {index}/{repeat} request_id={request['request_id']} method={method}", file=log, flush=True)
                pending = True
                # 整个发送/等待共用一个截止时间，heartbeat 不重置计时。
                async with asyncio.timeout(timeout):
                    await ws.send(payload)
                    while True:
                        message = await receive(ws)
                        kind = message.get("type")
                        if kind == "heartbeat":
                            seq, cid = message.get("seq"), message.get("connection_id")
                            if (not isinstance(cid, str) or not cid or type(seq) is not int or
                                seq <= last_seq or (connection_id is not None and cid != connection_id)):
                                raise ClientError("heartbeat 的连接标识或序号无效")
                            connection_id, last_seq = cid, seq
                            print("heartbeat " + json.dumps(message, ensure_ascii=False), file=log, flush=True)
                            continue
                        if kind not in ("result", "error"):
                            raise ClientError(f"等待结果时收到非预期消息：{kind!r}")
                        if message.get("request_id") != request["request_id"]:
                            raise ClientError("响应 request_id 与当前请求不匹配")
                        if kind == "error":
                            pending = False
                            raise ClientError("服务端拒绝请求：" + json.dumps(message.get("error"), ensure_ascii=False))
                        validate_result(message, data)
                        pending = False
                        break
                print(f"result {message['status']} stats={message['stats']} timing={message['timing']}", file=log, flush=True)
                if on_result is not None:
                    on_result(index, message)
            return message
    except (TimeoutError, ConnectionClosed) as exc:
        state = "请求结果未知，未自动重发；确认板端状态后可手动重新运行" if pending else "建联或等待 hello 失败"
        raise ClientError(f"{state}：{type(exc).__name__} {exc}") from exc


def positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return value


def positive_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("必须是有限正数")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:8080/vta", help="板端 WebSocket 地址")
    parser.add_argument("--input", type=Path, default=ROOT.parent / "docs/cleandata_ms.csv", help="输入 CSV，默认仓库示例数据")
    parser.add_argument("--output", type=Path, help="输出 CSV，默认 example/output/<输入名>.<算法>.csv")
    parser.add_argument("--method", default="mean", help="单个降噪算法，默认 mean")
    parser.add_argument("--params", default="{}", help='参数 JSON 对象，例如 \'{"window_size":5}\'')
    parser.add_argument("--repeat", type=positive_int, default=1, help="同一连接串行重复次数，每次发送原始数据")
    parser.add_argument("--timeout", type=positive_float, default=150, help="每次请求发送及等待总秒数，默认 150")
    parser.add_argument("--save-json", action="store_true", help="同时保存完整响应到同名 .json 文件")
    args = parser.parse_args()
    try:
        if not args.method or not all(c.isascii() and (c.isalnum() or c == "_") for c in args.method):
            raise ValueError("method 只能包含 ASCII 字母、数字和下划线")
        data, params = read_csv(args.input), parse_params(args.params)
        output = args.output or ROOT / "output" / f"{args.input.stem}.{args.method}.csv"
        if output.suffix.lower() != ".csv":
            raise ValueError("输出文件必须使用 .csv 后缀")
        def path_for(index):
            return output if args.repeat == 1 else output.with_name(f"{output.stem}.{index:03d}{output.suffix}")
        for index in range(1, args.repeat + 1):
            if path_for(index).resolve() == args.input.resolve():
                raise ValueError("输出路径不能覆盖输入 CSV")
        partial = False
        def save(index, response):
            nonlocal partial
            partial |= response["status"] == "partial"
            path = path_for(index)
            save_csv(path, response["data"])
            print(f"已保存：{path.resolve()}")
            if args.save_json:
                atomic_write(path.with_suffix(".json"), lambda stream: json.dump(
                    response, stream, ensure_ascii=False, allow_nan=False, indent=2))
            if response["status"] == "partial":
                print("部分通道存在降级或异常，请检查 column_results：", file=sys.stderr)
                for column in response["column_results"]:
                    print(json.dumps(column, ensure_ascii=False), file=sys.stderr)
        asyncio.run(denoise(args.url, data, args.method, params, timeout=args.timeout,
                            repeat=args.repeat, on_result=save))
        return 2 if partial else 0
    except (ValueError, OSError, csv.Error, WebSocketException, OverflowError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断并关闭连接；若已发送请求且未收到结果，则结果未知。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
