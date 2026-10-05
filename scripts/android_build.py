#!/usr/bin/env python3
"""Isolated Android exports. Packaging validation is NOT a device-playability test."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets as secure_random
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Mapping, Sequence

try:
    from scripts.android_manifest import apk_analyzer, validate_launcher
except ModuleNotFoundError:
    from android_manifest import apk_analyzer, validate_launcher

ENGINE = "4.7.2"
BUILD_TOOLS = "36.0.0"
PACKAGE = "io.github.cheshmakzanoonops.kartlab"
ROOT = Path(__file__).resolve().parents[1]
ERROR = re.compile(r"(?im)^\s*(?:SCRIPT ERROR:|ERROR:|FAILURE:|FATAL EXCEPTION:)|Parse Error|BUILD FAILED")


def redact(text: str, secrets: Sequence[str]) -> str:
    for value in sorted(set(secrets), key=len, reverse=True):
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def release_credentials(env: Mapping[str, str]) -> tuple[Path, str, str]:
    names = ["GODOT_ANDROID_KEYSTORE_RELEASE_" + part for part in ("PATH", "USER", "PASSWORD")]
    if not all(env.get(name) for name in names):
        raise ValueError("Release requires existing owner-controlled PATH, USER and PASSWORD signing variables")
    path = Path(env[names[0]]).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Release keystore does not exist")
    return path, env[names[1]], env[names[2]]


def preflight(sdk: Path, java: Path, templates: Path) -> None:
    required = [apk_analyzer(sdk), sdk / "platform-tools/adb", sdk / "platforms/android-36/android.jar"]
    required += [sdk / "build-tools" / BUILD_TOOLS / name for name in ("aapt", "apksigner", "zipalign")]
    required += [java / "bin" / name for name in ("java", "keytool", "jarsigner")]
    required += [templates / name for name in ("android_debug.apk", "android_release.apk", "android_source.zip")]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("Missing build dependencies:\n" + "\n".join(missing))


def validate_archive(path: Path, kind: str, abi: str, *, require_signature: bool = True) -> str:
    if kind not in ("apk", "aab") or abi not in ("arm64-v8a", "x86_64"):
        raise ValueError("Unsupported archive type or ABI")
    if not zipfile.is_zipfile(path):
        raise ValueError("Output is not a ZIP-format Android package")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("Duplicate or corrupt package entries")
        prefix = "base/" if kind == "aab" else ""
        if any(name.startswith('/') or '\\' in name or '..' in Path(name).parts for name in names):
            raise ValueError("Unsafe Android archive member path")
        project_module = "base"
        if kind == "aab":
            projects = [name for name in names if name.endswith("/assets/project.binary")]
            if len(projects) != 1 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*/assets/project\.binary", projects[0]):
                raise ValueError("AAB requires one unambiguous Godot project module")
            project_module = projects[0].split('/')[0]
            required = [projects[0], f"base/lib/{abi}/libgodot_android.so"]
            if project_module != "base":
                required += [project_module + "/manifest/AndroidManifest.xml", project_module + "/assets.pb"]
                if any(name.startswith((project_module + '/dex/', project_module + '/lib/')) for name in names):
                    raise ValueError("Project asset pack must not contain executable code")
        else:
            required = ["assets/project.binary", f"lib/{abi}/libgodot_android.so"]
        required += ["BundleConfig.pb", "base/manifest/AndroidManifest.xml", "base/dex/classes.dex"] if kind == "aab" else ["AndroidManifest.xml", "classes.dex"]
        if any(name not in names or archive.getinfo(name).file_size == 0 for name in required):
            raise ValueError("Package is missing nonempty manifest, DEX, engine or Godot project")
        architectures = {name.split("/")[1 + bool(prefix)] for name in names if name.startswith(prefix + "lib/") and name.endswith(".so")}
        if architectures != {abi}:
            raise ValueError("Unexpected packaged ABIs: " + repr(architectures))
        if kind == "aab" and require_signature:
            for suffix in (".SF", ".RSA"):
                if not any(name.startswith("META-INF/") and name.endswith(suffix) and archive.getinfo(name).file_size > 0 for name in names):
                    raise ValueError("AAB has no RSA JAR-signing records; cryptographic verification must follow")
        return project_module


def validate_asset_pack_manifest(text: str, module: str, package: str) -> None:
    """The project must be available at launch, not fast-follow or on-demand.

    Structure alone is not enough: build() obtains this XML through bundletool
    from the same signed AAB, then checks the actual pack delivery declaration.
    """
    root = ET.fromstring(text)
    dist = "{http://schemas.android.com/apk/distribution}"
    modules = root.findall(dist + "module")
    if root.tag != "manifest" or root.get("package") != package or root.get("split") != module or len(modules) != 1:
        raise ValueError("Asset pack manifest identity does not match the project module")
    pack = modules[0]
    deliveries = pack.findall(dist + "delivery")
    fusing = pack.findall(dist + "fusing")
    if (pack.get(dist + "type") != "asset-pack" or len(deliveries) != 1 or
            len(list(deliveries[0])) != 1 or list(deliveries[0])[0].tag != dist + "install-time" or
            len(list(deliveries[0])[0]) != 0 or len(fusing) != 1 or fusing[0].get(dist + "include") != "true"):
        raise ValueError("Godot project requires an unconditional fused install-time asset pack")


def sign_unsigned_bundle(path: Path, java: Path, env: Mapping[str, str], secrets: Sequence[str], *, release: bool) -> None:
    """Sign an unsigned Godot AAB using the already selected identity only.

    Existing signatures are never replaced. Partial records fail, and full JAR
    signature verification still follows. Release never falls back to debug.
    """
    with zipfile.ZipFile(path) as archive:
        signatures = [name for name in archive.namelist() if name.startswith("META-INF/") and
                      name.upper().endswith((".SF", ".RSA", ".EC", ".DSA"))]
        if signatures:
            if not any(name.endswith(".SF") for name in signatures) or not any(name.endswith(".RSA") for name in signatures):
                raise ValueError("AAB has partial or unsupported signing records; refusing to replace them")
            return
    prefix = "GODOT_ANDROID_KEYSTORE_" + ("RELEASE" if release else "DEBUG") + "_"
    if not all(env.get(prefix + field) for field in ("PATH", "USER", "PASSWORD")):
        raise ValueError("AAB signing requires the selected existing identity; no fallback is allowed")
    run([java / "bin/jarsigner", "-keystore", env[prefix + "PATH"],
         "-storepass:env", prefix + "PASSWORD", "-keypass:env", prefix + "PASSWORD",
         "-digestalg", "SHA-256", "-sigalg", "SHA256withRSA", "-sigfile", "KARTLAB",
         path, env[prefix + "USER"]], env, secrets, 120)


def validate_badging(text: str, package: str, *, require_launcher: bool = True) -> None:
    required = [rf"(?m)^package: name='{re.escape(package)}'", r"(?m)^sdkVersion:'24'", r"(?m)^targetSdkVersion:'36'", r"(?m)^launchable-activity: name='[^']+'"]
    if not require_launcher:
        # Only build() opts out: it separately validates decoded manifest XML.
        required = required[:-1]
    if not all(re.search(pattern, text) for pattern in required):
        raise ValueError("APK package, minimum/target SDK or launcher does not match export policy")


def validate_manifest(text: str, package: str) -> None:
    root = ET.fromstring(text)
    android = "{http://schemas.android.com/apk/res/android}"
    uses = root.find("uses-sdk")
    filters = root.findall("application/activity/intent-filter") + root.findall("application/activity-alias/intent-filter")
    launcher = any(any(e.get(android + "name") == "android.intent.action.MAIN" for e in f.findall("action")) and any(e.get(android + "name") == "android.intent.category.LAUNCHER" for e in f.findall("category")) for f in filters)
    if root.get("package") != package or uses is None or uses.get(android + "minSdkVersion") != "24" or uses.get(android + "targetSdkVersion") != "36" or not launcher:
        raise ValueError("AAB manifest does not match package, API or launcher policy")


def run(command: Sequence[str | Path], env: Mapping[str, str], secrets: Sequence[str], timeout: int = 900) -> str:
    args = [str(arg) for arg in command]
    print("RUN: " + redact(" ".join(args), secrets), flush=True)
    result = subprocess.run(args, env=dict(env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=timeout, check=False)
    output = result.stdout
    print(redact(output, secrets), end="" if output.endswith("\n") else "\n", flush=True)
    if result.returncode or ERROR.search(output):
        raise ValueError(f"Build command failed (exit {result.returncode}); inspect the redacted output above")
    return output


def source_digest(project: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(project.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(project).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def build(args: argparse.Namespace) -> dict:
    sdk, java, templates = (Path(value).expanduser().resolve() for value in (args.sdk, args.java, args.templates))
    release = args.target == "release-aab"
    # Release cannot accidentally fall back to an ephemeral debug identity.
    signing = release_credentials(os.environ) if release else None
    preflight(sdk, java, templates)
    output = Path(args.output).expanduser().resolve()
    kind = "aab" if args.target.endswith("aab") else "apk"
    if output.suffix != "." + kind or output.exists() or output.with_suffix(output.suffix + ".json").exists():
        raise ValueError("Use a new output path with the requested package extension; existing evidence is never overwritten")
    bundletool = Path(args.bundletool).expanduser().resolve()
    if kind == "aab" and not bundletool.is_file():
        raise ValueError("AAB verification requires --bundletool /path/to/bundletool-all-1.18.1.jar")
    env = dict(os.environ)
    env.update({"JAVA_HOME": str(java), "ANDROID_HOME": str(sdk), "ANDROID_SDK_ROOT": str(sdk), "GODOT_SILENCE_ROOT_WARNING": "1"})
    env["PATH"] = str(java / "bin") + os.pathsep + env.get("PATH", "")
    secrets = [value for key, value in env.items() if key.startswith("GODOT_ANDROID_KEYSTORE_") and key.endswith("PASSWORD")]
    version = run([args.godot, "--version"], env, secrets, 30).strip()
    if not version.startswith(ENGINE + ".stable"):
        raise ValueError("Use the pinned Godot " + ENGINE + " stable engine and matching templates")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kart-android-") as directory:
        temp = Path(directory)
        project = temp / "port"
        shutil.copytree(ROOT / "port", project, ignore=shutil.ignore_patterns(".godot", "android", "tests", "*.keystore", "*.jks", "*.p12"))
        project_hash = source_digest(project)
        # Both editor paths and template lookup are isolated from user settings.
        gradle = temp / "gradle"
        gradle.mkdir()
        (gradle / "gradle.properties").write_text("org.gradle.daemon=false\norg.gradle.jvmargs=-Xmx3072m\n")
        env["GRADLE_USER_HOME"] = str(gradle)
        config = temp / "config/godot"
        config.mkdir(parents=True)
        data = temp / ("data/godot/export_templates/" + ENGINE + ".stable")
        data.parent.mkdir(parents=True)
        data.symlink_to(templates, target_is_directory=True)
        env.update({"XDG_CONFIG_HOME": str(temp / "config"), "XDG_DATA_HOME": str(temp / "data"), "XDG_CACHE_HOME": str(temp / "cache")})
        settings = '[gd_resource type="EditorSettings" format=3]\n\n[resource]\n'
        for name, value in (("java_sdk_path", str(java)), ("android_sdk_path", str(sdk))):
            settings += "export/android/" + name + " = " + json.dumps(value) + "\n"
        (config / "editor_settings-4.7.tres").write_text(settings)
        if not release:
            prefix = "GODOT_ANDROID_KEYSTORE_DEBUG_"
            supplied = [env.get(prefix + part) for part in ("PATH", "USER", "PASSWORD")]
            if any(supplied) and not all(supplied):
                raise ValueError("Supply all three debug signing variables or none")
            if all(supplied):
                if not Path(supplied[0]).is_file():
                    raise ValueError("Supplied debug keystore does not exist")
            else:
                key = temp / "test.keystore"
                password = secure_random.token_hex(24)
                env.update({prefix + "PATH": str(key), prefix + "USER": "androiddebugkey", prefix + "PASSWORD": password})
                secrets.append(password)
                run([java / "bin/keytool", "-genkeypair", "-keystore", key, "-alias", "androiddebugkey", "-storepass:env", prefix + "PASSWORD", "-keypass:env", prefix + "PASSWORD", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000", "-dname", "CN=Android Debug,O=Kart Lab,C=CA"], env, secrets, 30)
                key.chmod(0o600)
        elif signing:
            env["GODOT_ANDROID_KEYSTORE_RELEASE_PATH"] = str(signing[0])
        abi = "x86_64" if args.target == "emulator-apk" else "arm64-v8a"
        if abi == "x86_64":
            preset_path = project / "export_presets.cfg"
            preset_path.write_text(preset_path.read_text().replace("architectures/arm64-v8a=true", "architectures/arm64-v8a=false").replace("architectures/x86_64=false", "architectures/x86_64=true"))
        run([args.godot, "--headless", "--path", project, "--import"], env, secrets, 240)
        preset = "Android Release" if release else ("Android Bundle Test" if kind == "aab" else "Android Test")
        # Godot only processes template installation as part of an export. A
        # standalone --editor/--quit command exits zero without installing it.
        run([args.godot, "--headless", "--path", project, "--install-android-build-template",
             "--export-release" if release else "--export-debug", preset, output], env, secrets)
        project_module = validate_archive(output, kind, abi, require_signature=(kind != "aab"))
        if kind == "aab":
            sign_unsigned_bundle(output, java, env, secrets, release=release)
            validate_archive(output, kind, abi)
        package = PACKAGE if release else PACKAGE + ".test"
        tools = sdk / "build-tools" / BUILD_TOOLS
        if kind == "apk":
            run([tools / "apksigner", "verify", "--verbose", "--print-certs", output], env, secrets, 60)
            run([tools / "zipalign", "-c", "-P", "16", "-v", "4", output], env, secrets, 60)
            validate_badging(run([tools / "aapt", "dump", "badging", output], env, secrets, 60), package, require_launcher=False)
            manifest = run([apk_analyzer(sdk), "manifest", "print", output], env, secrets, 120)
            validate_manifest(manifest, package)
            launcher = validate_launcher(manifest)
        else:
            verification = run([java / "bin/jarsigner", "-verify", output], env, secrets, 60)
            if "jar verified." not in verification.lower() or "unsigned entries" in verification.lower() or "treated as unsigned" in verification.lower():
                raise ValueError("AAB JAR signature was not verified")
            run([java / "bin/java", "-jar", bundletool, "validate", "--bundle=" + str(output)], env, secrets, 120)
            manifest = run([java / "bin/java", "-jar", bundletool, "dump", "manifest", "--bundle=" + str(output), "--module=base"], env, secrets, 120)
            validate_manifest(manifest, package)
            launcher = validate_launcher(manifest)
            if project_module != "base":
                pack_manifest = run([java / "bin/java", "-jar", bundletool, "dump", "manifest",
                                     "--bundle=" + str(output), "--module=" + project_module], env, secrets, 120)
                validate_asset_pack_manifest(pack_manifest, project_module, package)
        report = {"status": "packaging-verified", "file": output.name, "bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "source_commit": env.get("GITHUB_SHA", "local-source; see source_digest"), "source_digest": project_hash, "godot": version, "package": package, "launcher": launcher, "abi": abi, "project_module": project_module, "min_sdk": 24, "target_sdk": 36, "test_signed": not release, "emulator_tested": False, "physical_device_tested": False, "complete_game": False}
        output.with_suffix(output.suffix + ".json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("debug-apk", "emulator-apk", "test-aab", "release-aab"), default="debug-apk")
    parser.add_argument("--output", required=True)
    parser.add_argument("--godot", default=shutil.which("godot") or "godot")
    parser.add_argument("--sdk", default=os.environ.get("ANDROID_SDK_ROOT", os.environ.get("ANDROID_HOME", "/missing-android-sdk")))
    parser.add_argument("--java", default=os.environ.get("JAVA_HOME", "/missing-java-sdk"))
    parser.add_argument("--templates", default=str(Path.home() / ".local/share/godot/export_templates" / (ENGINE + ".stable")))
    parser.add_argument("--bundletool", default=os.environ.get("BUNDLETOOL", "/missing-bundletool.jar"))
    args = parser.parse_args()
    try:
        build(args)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, ET.ParseError, zipfile.BadZipFile) as error:
        # Do not echo exception output that could contain a signing command.
        secrets = [v for k, v in os.environ.items() if k.startswith("GODOT_ANDROID_KEYSTORE_") and k.endswith("PASSWORD")]
        print("ANDROID BUILD FAILED: " + redact(str(error), secrets), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
