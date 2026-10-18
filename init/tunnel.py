import logging
import queue
import re
import subprocess
import sys
import threading
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TUNNEL_TIMEOUT = 20
RSD_PATTERN = re.compile(r"--rsd (\S+) (\d+)")


def _read_output(stream, output_queue):
    """Drain the child's stdout so it never blocks on a full pipe."""
    try:
        for line in stream:
            output_queue.put(line)
    finally:
        output_queue.put(None)


def stop(process):
    """Terminate the tunnel process, falling back to a process-tree kill."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            process.kill()
            process.wait(timeout=5)


def tunnel():
    """Start the RSD tunnel and return (process, address, port).

    Returns (None, None, None) when the tunnel cannot be established. The
    returned process is the real pymobiledevice3 child, so callers can always
    terminate it through stop() without leaving an orphan behind.
    """
    command = [sys.executable, "-m", "pymobiledevice3", "lockdown", "start-tunnel"]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output_queue = queue.Queue()
    threading.Thread(target=_read_output, args=(process.stdout, output_queue), daemon=True).start()

    logging.info("Tunnel started")

    deadline = time.monotonic() + TUNNEL_TIMEOUT
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"隧道建立超时（{TUNNEL_TIMEOUT}s）")

            try:
                line = output_queue.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"隧道建立超时（{TUNNEL_TIMEOUT}s）")

            if line is None:
                raise RuntimeError("隧道进程意外退出")

            line = line.strip()
            if line:
                logging.info(line)

            match = RSD_PATTERN.search(line)
            if match:
                address, port = match.group(1), int(match.group(2))
                logging.info(f"RSD Address: {address}, RSD Port: {port}")
                return process, address, port
    except Exception as e:
        logging.error(f"❌ 隧道建立失败: {e}")
        stop(process)
        return None, None, None
