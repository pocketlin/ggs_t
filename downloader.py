#!/usr/bin/env python
"""
Multi-threaded file downloader with configurable proxy support.
Usage: python downloader.py <url> [--output <path>] [--threads <n>]
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class ProgressTracker:
    def __init__(self, total_size):
        self.total_size = total_size
        self.downloaded = 0
        self.lock = threading.Lock()
        self.start_time = time.time()
        self._stop_timer = threading.Event()
        self._display_thread = None

    def update(self, size):
        with self.lock:
            self.downloaded += size

    def _format_size(self, size):
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def _format_speed(self, speed):
        return f"{self._format_size(speed)}/s"

    def _display_loop(self):
        while not self._stop_timer.is_set():
            with self.lock:
                downloaded = self.downloaded
            elapsed = max(time.time() - self.start_time, 0.001)
            speed = downloaded / elapsed
            pct = (downloaded / self.total_size * 100) if self.total_size else 0
            bar_width = 30
            filled = int(bar_width * pct / 100)
            bar = "#" * filled + "-" * (bar_width - filled)
            sys.stdout.write(
                f"\r  [{bar}] {pct:5.1f}%  "
                f"{self._format_size(downloaded)}/{self._format_size(self.total_size)}  "
                f"{self._format_speed(speed)}   "
            )
            sys.stdout.flush()
            if downloaded >= self.total_size > 0:
                break
            time.sleep(0.2)

    def start_display(self):
        self._display_thread = threading.Thread(target=self._display_loop, daemon=True)
        self._display_thread.start()

    def stop_display(self):
        self._stop_timer.set()
        if self._display_thread:
            self._display_thread.join()
        elapsed = time.time() - self.start_time
        speed = self.downloaded / elapsed if elapsed > 0 else 0
        sys.stdout.write(
            f"\r  [{'#' * 30}] 100.0%  "
            f"{self._format_size(self.downloaded)}/{self._format_size(self.total_size)}  "
            f"{self._format_speed(speed)}\n"
        )
        sys.stdout.flush()


class Downloader:
    DEFAULT_CONFIG = {
        "proxy": {
            "enabled": False,
            "http": "",
            "https": "",
        },
        "threads": 8,
        "chunk_size": 1024 * 1024,  # 1MB per chunk
        "retry": {
            "max_retries": 3,
            "backoff_factor": 1,
        },
        "timeout": 30,
        "verify_ssl": True,
    }

    def __init__(self, config_path=None):
        self.config = self._load_config(config_path)
        self.session = self._build_session()

    def _load_config(self, config_path):
        config = dict(self.DEFAULT_CONFIG)
        if config_path is None:
            config_paths = [
                Path(__file__).parent / "config.json",
                Path.cwd() / "config.json",
            ]
            for p in config_paths:
                if p.exists():
                    config_path = str(p)
                    break

        if config_path and os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            self._deep_merge(config, user_config)
        return config

    def _deep_merge(self, base, override):
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def _build_session(self):
        session = requests.Session()

        # Proxy
        proxy_config = self.config.get("proxy", {})
        if proxy_config.get("enabled"):
            proxies = {}
            if proxy_config.get("http"):
                proxies["http"] = proxy_config["http"]
            if proxy_config.get("https"):
                proxies["https"] = proxy_config["https"]
            if proxies:
                session.proxies.update(proxies)

        # Retry
        retry_config = self.config.get("retry", {})
        retry = Retry(
            total=retry_config.get("max_retries", 3),
            backoff_factor=retry_config.get("backoff_factor", 1),
            status_forcelist=[500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        session.verify = self.config.get("verify_ssl", True)
        return session

    def _get_file_size(self, url):
        resp = self.session.head(
            url, timeout=self.config.get("timeout", 30)
        )
        resp.raise_for_status()
        content_length = resp.headers.get("content-length")
        if content_length is None:
            raise ValueError("Server does not provide content-length. Cannot use multi-threaded download.")
        accept_ranges = resp.headers.get("accept-ranges", "").lower()
        if accept_ranges != "bytes":
            print("  [!] Warning: Server may not support range requests. Falling back to single-threaded download.")
            return int(content_length), False
        return int(content_length), True

    def _download_chunk(self, url, start, end, output_path):
        headers = {"Range": f"bytes={start}-{end}"}
        resp = self.session.get(
            url,
            headers=headers,
            stream=True,
            timeout=self.config.get("timeout", 30),
        )
        resp.raise_for_status()
        with open(output_path, "r+b") as f:
            f.seek(start)
            for data in resp.iter_content(chunk_size=8192):
                if data:
                    f.write(data)
                    yield len(data)

    def _download_single(self, url, output_path, progress):
        resp = self.session.get(
            url,
            stream=True,
            timeout=self.config.get("timeout", 30),
        )
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for data in resp.iter_content(chunk_size=8192):
                if data:
                    f.write(data)
                    progress.update(len(data))

    def download(self, url, output=None, threads=None):
        if threads is None:
            threads = self.config.get("threads", 8)

        print(f"  [+] URL: {url}")
        print(f"  [+] Connecting...")

        try:
            total_size, supports_ranges = self._get_file_size(url)
        except Exception as e:
            print(f"  [-] Failed to get file info: {e}")
            sys.exit(1)

        filename = output
        if not filename:
            parsed = urlparse(url)
            path = parsed.path
            if path and "/" in path:
                filename = os.path.basename(path)
            if not filename:
                filename = "download"
        print(f"  [+] File: {filename}")
        print(f"  [+] Size: {total_size / 1024 / 1024:.1f} MB")
        print(f"  [+] Threads: {threads}")
        print(f"  [+] Proxy: {'ON' if self.config['proxy'].get('enabled') else 'OFF'}")

        progress = ProgressTracker(total_size)
        progress.start_display()

        try:
            if not supports_ranges or threads <= 1 or total_size < threads * 1024 * 1024:
                self._download_single(url, filename, progress)
            else:
                with open(filename, "wb") as f:
                    f.truncate(total_size)

                chunk_size = (total_size + threads - 1) // threads
                tasks = []
                for i in range(threads):
                    start = i * chunk_size
                    end = min(start + chunk_size - 1, total_size - 1)
                    if start >= total_size:
                        break
                    tasks.append((start, end))

                with ThreadPoolExecutor(max_workers=threads) as executor:
                    futures = {}
                    for start, end in tasks:
                        future = executor.submit(self._download_chunk_wrapper, url, start, end, filename, progress)
                        futures[future] = (start, end)

                    for future in as_completed(futures):
                        future.result()

            progress.stop_display()
            actual_size = os.path.getsize(filename)
            if actual_size != total_size:
                print(f"  [!] Warning: Size mismatch (expected {total_size}, got {actual_size})")
            else:
                print(f"  [+] Download complete: {os.path.abspath(filename)}")
        except Exception as e:
            progress.stop_display()
            print(f"  [-] Error: {e}")
            sys.exit(1)

    def _download_chunk_wrapper(self, url, start, end, output_path, progress):
        for chunk_size in self._download_chunk(url, start, end, output_path):
            progress.update(chunk_size)


def main():
    parser = argparse.ArgumentParser(
        description="Multi-threaded file downloader",
    )
    parser.add_argument("url", help="URL to download")
    parser.add_argument("--output", "-o", default=None, help="Output file path")
    parser.add_argument("--threads", "-t", type=int, default=None, help="Number of threads (default from config)")
    parser.add_argument("--config", "-c", default=None, help="Path to config.json")
    args = parser.parse_args()

    downloader = Downloader(config_path=args.config)
    downloader.download(args.url, output=args.output, threads=args.threads)


if __name__ == "__main__":
    main()
