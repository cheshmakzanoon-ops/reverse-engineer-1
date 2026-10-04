#!/usr/bin/env python3
"""Install/launch/background/relaunch smoke on an explicitly selected emulator.

Not a full race, rendering-quality, physical-device or 60 FPS acceptance test.
Never chooses or uninstalls a physical device automatically.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

ERROR = re.compile(r'SCRIPT ERROR:|Parse Error|FATAL EXCEPTION|Fatal signal|ANR in|ERROR:.*(?:res://|Resource|load)', re.I)


def smoke(adb: str, serial: str, apk: Path, output: Path) -> None:
    if not serial.startswith('emulator-'):
        raise ValueError('This automated smoke only targets an explicitly named emulator')
    if not apk.is_file():
        raise ValueError('APK does not exist')
    output.mkdir(parents=True, exist_ok=False)
    package = 'io.github.cheshmakzanoonops.kartlab.test'
    def command(*args: str) -> str:
        result = subprocess.run([adb, '-s', serial, *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90)
        return result.stdout
    if command('shell', 'getprop', 'ro.kernel.qemu').strip() != '1':
        raise ValueError('Selected target is not an Android emulator')
    command('logcat', '-c')
    install = command('install', '-r', str(apk.resolve()))
    if 'Success' not in install:
        raise ValueError('APK install did not report success')
    pids = []
    try:
        for cycle in range(3):
            command('shell', 'am', 'force-stop', package)
            launch = command('shell', 'am', 'start', '-W', '-n', package + '/org.godotengine.godot.GodotApp')
            if 'Status: ok' not in launch:
                raise ValueError('Launcher failed: ' + launch)
            time.sleep(6)
            pid = command('shell', 'pidof', package).strip()
            if not pid:
                raise ValueError('Game process did not survive launch')
            pids.append(pid)
            command('shell', 'input', 'keyevent', 'KEYCODE_HOME')
            time.sleep(1)
            command('shell', 'am', 'start', '-W', '-n', package + '/org.godotengine.godot.GodotApp')
            time.sleep(2)
            if command('shell', 'pidof', package).strip() != pid:
                raise ValueError('Game process died during background/resume')
            image = subprocess.run([adb, '-s', serial, 'exec-out', 'screencap', '-p'], check=True, stdout=subprocess.PIPE, timeout=30).stdout
            if not image.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Screenshot capture is not PNG data')
            (output / f'launch-{cycle}.png').write_bytes(image)
        logs = command('logcat', '-d', '-v', 'threadtime')
        (output / 'logcat.txt').write_text(logs)
        if ERROR.search(logs):
            raise ValueError('Android or Godot errors found in smoke logcat')
        report = {'status': 'emulator-launch-smoke-passed', 'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(), 'serial': serial, 'api': command('shell', 'getprop', 'ro.build.version.sdk').strip(), 'abi': command('shell', 'getprop', 'ro.product.cpu.abi').strip(), 'launches': 3, 'processes': pids, 'physical_device_tested': False, 'full_race_tested': False}
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    finally:
        # Logs survive an assertion failure; no cleanup may mask that failure.
        try:
            (output / 'logcat.txt').write_text(command('logcat', '-d', '-v', 'threadtime'))
        except subprocess.SubprocessError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--serial', required=True)
    parser.add_argument('--apk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    smoke(args.adb, args.serial, args.apk, args.output)


if __name__ == '__main__':
    main()
