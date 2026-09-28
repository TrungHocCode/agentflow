"""Optional, best-effort local CPU, RAM, and NVIDIA GPU sampling."""

from __future__ import annotations

import asyncio
import ctypes
import os
import shutil
from typing import Any


class SystemResourceSampler:
    """Sample host utilization without making resource monitoring mandatory."""

    def __init__(self) -> None:
        self._previous_cpu: tuple[int, int] | None = None

    async def sample(self) -> dict[str, Any]:
        cpu_percent, ram_used_mb = self._sample_cpu_and_ram()
        gpu = await self._sample_nvidia_gpu()
        return {
            "cpu_utilization_percent": cpu_percent,
            "ram_used_mb": ram_used_mb,
            **gpu,
        }

    def _sample_cpu_and_ram(self) -> tuple[float | None, float | None]:
        if os.name == "nt":
            return self._sample_windows_cpu_and_ram()
        try:
            import psutil

            return (
                round(psutil.cpu_percent(interval=None), 2),
                round(psutil.virtual_memory().used / (1024 * 1024), 2),
            )
        except ImportError:
            return None, None

    def _sample_windows_cpu_and_ram(self) -> tuple[float | None, float | None]:
        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

            def to_int(self) -> int:
                return (self.high << 32) | self.low

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_uint32),
                ("memory_load", ctypes.c_uint32),
                ("total_phys", ctypes.c_uint64),
                ("available_phys", ctypes.c_uint64),
                ("total_page_file", ctypes.c_uint64),
                ("available_page_file", ctypes.c_uint64),
                ("total_virtual", ctypes.c_uint64),
                ("available_virtual", ctypes.c_uint64),
                ("available_extended_virtual", ctypes.c_uint64),
            ]

        kernel32 = ctypes.windll.kernel32
        idle, kernel, user = FileTime(), FileTime(), FileTime()
        cpu_percent = None
        if kernel32.GetSystemTimes(
            ctypes.byref(idle),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            current_cpu = (idle.to_int(), kernel.to_int() + user.to_int())
            if self._previous_cpu is not None:
                idle_delta = current_cpu[0] - self._previous_cpu[0]
                total_delta = current_cpu[1] - self._previous_cpu[1]
                if total_delta > 0:
                    cpu_percent = round(
                        max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)),
                        2,
                    )
            self._previous_cpu = current_cpu

        memory = MemoryStatus()
        memory.length = ctypes.sizeof(MemoryStatus)
        ram_used_mb = None
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            ram_used_mb = round(
                (memory.total_phys - memory.available_phys) / (1024 * 1024),
                2,
            )
        return cpu_percent, ram_used_mb

    @staticmethod
    async def _sample_nvidia_gpu() -> dict[str, Any]:
        if shutil.which("nvidia-smi") is None:
            return {}
        try:
            process = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=2)
        except (OSError, asyncio.TimeoutError):
            return {}
        if process.returncode != 0 or not stdout:
            return {}
        try:
            name, utilization, memory_used = stdout.decode("utf-8").splitlines()[0].split(",", 2)
            return {
                "gpu_name": name.strip(),
                "gpu_utilization_percent": float(utilization.strip()),
                "vram_used_mb": float(memory_used.strip()),
            }
        except (IndexError, ValueError, UnicodeDecodeError):
            return {}


def summarize_resource_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate optional host snapshots into the evaluation record resource shape."""

    def numbers(key: str) -> list[float]:
        return [
            float(sample[key])
            for sample in samples
            if isinstance(sample.get(key), (int, float))
        ]

    cpu = numbers("cpu_utilization_percent")
    ram = numbers("ram_used_mb")
    gpu_util = numbers("gpu_utilization_percent")
    vram = numbers("vram_used_mb")
    gpu_names = [str(sample["gpu_name"]) for sample in samples if sample.get("gpu_name")]
    return {
        "cpu_utilization_percent": round(sum(cpu) / len(cpu), 2) if cpu else None,
        "peak_ram_mb": round(max(ram), 2) if ram else None,
        "gpu_name": gpu_names[0] if gpu_names else None,
        "gpu_utilization_percent": round(sum(gpu_util) / len(gpu_util), 2) if gpu_util else None,
        "peak_vram_mb": round(max(vram), 2) if vram else None,
        "sample_count": len(samples),
    }
