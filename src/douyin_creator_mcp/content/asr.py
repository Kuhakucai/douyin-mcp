"""Controlled FFmpeg execution and strictly classified local faster-whisper ASR."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..errors import ASR_FAILED, CAPABILITY_MISSING, PROCESS_FAILED, AppError
from .models import AsrResult, AsrSegment

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _JobBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JobExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JobBasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]


class _WindowsJob:
    """Kill-on-close Job Object assigned before a suspended child is resumed."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9
    _BASIC_ACCOUNTING_INFORMATION = 1
    _BASIC_PROCESS_ID_LIST = 3

    def __init__(self, process: subprocess.Popen[str]):
        if os.name != "nt":
            raise RuntimeError("Windows Job Objects are only available on Windows.")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll")
        self.kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self.kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self.kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self.kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        self.kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        self.kernel32.TerminateJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
        ]
        self.kernel32.TerminateJobObject.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.handle = self.kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = _JobExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
            if not self.kernel32.SetInformationJobObject(
                self.handle,
                self._EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel32.AssignProcessToJobObject(
                self.handle, wintypes.HANDLE(int(process._handle))
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def resume(self, process: subprocess.Popen[str]) -> None:
        self.ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        self.ntdll.NtResumeProcess.restype = wintypes.LONG
        status = self.ntdll.NtResumeProcess(wintypes.HANDLE(int(process._handle)))
        if status != 0:
            raise OSError(f"NtResumeProcess failed with NTSTATUS 0x{status & 0xFFFFFFFF:08x}")

    def active_processes(self) -> int:
        accounting = _JobBasicAccountingInformation()
        if not self.kernel32.QueryInformationJobObject(
            self.handle,
            self._BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(accounting.ActiveProcesses)

    def process_ids(self) -> list[int]:
        capacity = 8192
        buffer = ctypes.create_string_buffer(
            ctypes.sizeof(wintypes.DWORD) * 2
            + ctypes.sizeof(ctypes.c_size_t) * capacity
        )
        if not self.kernel32.QueryInformationJobObject(
            self.handle,
            self._BASIC_PROCESS_ID_LIST,
            buffer,
            ctypes.sizeof(buffer),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        counts = (wintypes.DWORD * 2).from_buffer(buffer)
        listed = int(counts[1])
        if listed > capacity:
            raise AppError(
                PROCESS_FAILED,
                "Windows process tree exceeded the cleanup tracking capacity.",
            )
        values = (ctypes.c_size_t * listed).from_buffer(
            buffer, ctypes.sizeof(wintypes.DWORD) * 2
        )
        return [int(value) for value in values]

    def _open_process_handles(self, process_ids: list[int]) -> list[Any]:
        handles = []
        for process_id in process_ids:
            handle = self.kernel32.OpenProcess(0x00100000, False, process_id)
            if handle:
                handles.append(handle)
        return handles

    def terminate_and_wait(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        handles = self._open_process_handles(self.process_ids())
        try:
            if self.active_processes() and not self.kernel32.TerminateJobObject(
                self.handle, 1
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            handles.extend(self._open_process_handles(self.process_ids()))
            for process_handle in handles:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                result = self.kernel32.WaitForSingleObject(
                    process_handle, remaining_ms
                )
                if result == 258:
                    raise AppError(
                        PROCESS_FAILED,
                        "Windows process tree did not terminate before the cleanup deadline.",
                    )
                if result == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            while self.active_processes():
                if time.monotonic() >= deadline:
                    raise AppError(
                        PROCESS_FAILED,
                        "Windows process tree did not terminate before the cleanup deadline.",
                    )
                time.sleep(0.02)
        finally:
            for process_handle in handles:
                self.kernel32.CloseHandle(process_handle)

    def close(self) -> None:
        handle, self.handle = getattr(self, "handle", None), None
        if handle:
            self.kernel32.CloseHandle(handle)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


class _BoundedTextBuffer:
    def __init__(self, limit: int = 65_536):
        self.limit = limit
        self.parts: list[str] = []
        self.size = 0
        self.lock = threading.Lock()

    def append(self, value: str) -> None:
        with self.lock:
            self.parts.append(value)
            self.size += len(value)
            while self.size > self.limit and self.parts:
                excess = self.size - self.limit
                if len(self.parts[0]) <= excess:
                    self.size -= len(self.parts.pop(0))
                else:
                    self.parts[0] = self.parts[0][excess:]
                    self.size -= excess

    def value(self) -> str:
        with self.lock:
            return "".join(self.parts)


class ControlledProcessRunner:
    def run(
        self,
        args: list[str],
        *,
        timeout: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> ProcessResult:
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004
            if os.name == "nt"
            else 0
        )
        job: _WindowsJob | None = None
        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
        except FileNotFoundError as exc:
            raise AppError(
                CAPABILITY_MISSING,
                f"Required local executable is unavailable: {Path(args[0]).name}",
            ) from exc
        if os.name == "nt":
            try:
                job = _WindowsJob(process)
                job.resume(process)
            except Exception as exc:
                try:
                    process.kill()
                    process.wait(2)
                finally:
                    if job is not None:
                        job.close()
                raise AppError(
                    PROCESS_FAILED,
                    "External process could not be placed in a Windows Job Object.",
                ) from exc
        stdout_buffer, stderr_buffer = _BoundedTextBuffer(), _BoundedTextBuffer()

        def drain(stream: Any, target: _BoundedTextBuffer) -> None:
            try:
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        return
                    target.append(chunk)
            finally:
                stream.close()

        readers = [
            threading.Thread(
                target=drain, args=(process.stdout, stdout_buffer), daemon=True
            ),
            threading.Thread(
                target=drain, args=(process.stderr, stderr_buffer), daemon=True
            ),
        ]
        for reader in readers:
            reader.start()
        started = time.monotonic()
        failure: AppError | None = None
        while process.poll() is None:
            if cancelled and cancelled():
                failure = AppError(
                    PROCESS_FAILED, "External process was cancelled.", retryable=True
                )
                self._terminate_tree(process, job)
                break
            if time.monotonic() - started > timeout:
                failure = AppError(
                    PROCESS_FAILED, "External process timed out.", retryable=True
                )
                self._terminate_tree(process, job)
                break
            time.sleep(0.05)
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            self._terminate_tree(process, job)
            process.wait(2)
        if job is not None:
            try:
                job.terminate_and_wait()
            finally:
                job.close()
        for reader in readers:
            reader.join(0.5 if failure is not None else 2)
        stdout, stderr = stdout_buffer.value(), stderr_buffer.value()
        if failure is not None:
            raise failure
        if process.returncode != 0:
            raise AppError(
                PROCESS_FAILED,
                f"External process failed with exit code {process.returncode}: {stderr}",
            )
        return ProcessResult(process.returncode, stdout, stderr)

    @staticmethod
    def _terminate_tree(
        process: subprocess.Popen[str], job: _WindowsJob | None = None
    ) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            if job is None:
                raise AppError(
                    PROCESS_FAILED,
                    "Windows process tree is not owned by a Job Object.",
                )
            job.terminate_and_wait()
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(2)
        except Exception:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class AudioExtractor:
    def __init__(
        self,
        runner: ControlledProcessRunner,
        executable: str = "ffmpeg",
        timeout: int = 600,
    ):
        self.runner = runner
        self.executable = executable
        self.timeout = timeout

    def extract(
        self,
        media_path: Path,
        wav_path: Path,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.runner.run(
                [
                    self.executable,
                    "-nostdin",
                    "-y",
                    "-i",
                    str(media_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(wav_path),
                ],
                timeout=self.timeout,
                cancelled=cancelled,
            )
            self.validate_wav(wav_path)
            return wav_path
        except Exception:
            wav_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def validate_wav(path: Path) -> int:
        try:
            with wave.open(str(path), "rb") as handle:
                if (
                    handle.getnchannels() != 1
                    or handle.getframerate() != 16_000
                    or handle.getsampwidth() != 2
                    or handle.getnframes() <= 0
                ):
                    raise ValueError("unexpected PCM format")
                return int(handle.getnframes() * 1000 / handle.getframerate())
        except (wave.Error, EOFError, ValueError) as exc:
            raise AppError(ASR_FAILED, "Extracted WAV is empty or invalid.") from exc


class FasterWhisperTranscriber:
    _models: dict[tuple[str, str, str], Any] = {}
    _lock = threading.Lock()

    def __init__(
        self,
        model_dir: Path,
        *,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        model_factory: Any | None = None,
    ):
        self.model_dir = model_dir
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.model_factory = model_factory

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "faster-whisper",
            "model_size": self.model_size,
            "local_model_available": self.model_dir.exists(),
        }

    def transcribe(
        self,
        wav_path: Path,
        *,
        title: str = "",
        cancelled: Callable[[], bool] | None = None,
    ) -> AsrResult:
        AudioExtractor.validate_wav(wav_path)
        model = self._model()
        hotwords = self._hotwords(title)
        try:
            iterator, info = model.transcribe(
                str(wav_path),
                beam_size=1,
                vad_filter=True,
                hotwords=hotwords or None,
            )
            segments: list[AsrSegment] = []
            for index, item in enumerate(iterator):
                if cancelled and cancelled():
                    raise AppError(ASR_FAILED, "ASR was cancelled.", retryable=True)
                text = str(getattr(item, "text", "") or "")
                if not text.strip():
                    continue
                segments.append(
                    AsrSegment(
                        len(segments),
                        max(0, int(float(item.start) * 1000)),
                        max(0, int(float(item.end) * 1000)),
                        text,
                        getattr(item, "avg_logprob", None),
                        getattr(item, "no_speech_prob", None),
                        getattr(info, "language", None),
                    )
                )
        except AppError:
            raise
        except Exception as exc:
            raise AppError(ASR_FAILED, f"Local ASR failed: {exc}", retryable=True) from exc
        return AsrResult(
            tuple(segments),
            getattr(info, "language", None),
            "faster-whisper",
            self.model_size,
            None,
        )

    def _model(self) -> Any:
        if not self.model_dir.exists():
            raise AppError(
                CAPABILITY_MISSING,
                "Configured local faster-whisper model directory does not exist.",
            )
        key = (str(self.model_dir.resolve()), self.device, self.compute_type)
        with self._lock:
            if key in self._models:
                return self._models[key]
            factory = self.model_factory
            if factory is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise AppError(
                        CAPABILITY_MISSING,
                        "faster-whisper is not installed; install the 'asr' extra.",
                    ) from exc
                factory = WhisperModel
            self._models[key] = factory(
                str(self.model_dir),
                device=self.device,
                compute_type=self.compute_type,
                local_files_only=True,
            )
            return self._models[key]

    @staticmethod
    def _hotwords(title: str, limit: int = 32) -> str:
        words = [part for part in title.replace("，", " ").replace("。", " ").split() if part]
        return " ".join(words[:limit])
