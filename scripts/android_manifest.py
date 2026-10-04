"""Validate actual manifest launchers, including exported Godot activity aliases.

AAPT's badging summary can omit a valid alias. It is not a substitute for the
actual decoded manifest, nor is manifest validation a substitute for a launch.
"""
from pathlib import Path
import xml.etree.ElementTree as ET

ANDROID = '{http://schemas.android.com/apk/res/android}'


def apk_analyzer(sdk: Path) -> Path:
    """Resolve only within the explicitly selected SDK, not an unrelated PATH."""
    latest = sdk / 'cmdline-tools/latest/bin/apkanalyzer'
    candidates = [latest] + sorted((sdk / 'cmdline-tools').glob('*/bin/apkanalyzer'), reverse=True)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError('Android SDK command-line tools with apkanalyzer are required')


def validate_launcher(text: str) -> str:
    root = ET.fromstring(text)
    package = root.get('package', '')
    app = root.find('application')
    if not package or app is None or app.get(ANDROID+'enabled', 'true') != 'true':
        raise ValueError('Missing or disabled Android application')

    def qualified(value: str | None) -> str:
        if not value or value.startswith('@'):
            raise ValueError('Launcher component name must be explicit')
        return package+value if value.startswith('.') else package+'.'+value if '.' not in value else value

    activities = {}
    names = set()
    launchers = []
    for component in app:
        if component.tag not in ('activity', 'activity-alias'):
            continue
        name = qualified(component.get(ANDROID+'name'))
        if name in names:
            raise ValueError('Duplicate Android activity/alias name: '+name)
        names.add(name)
        if component.tag == 'activity':
            activities[name] = component
        target = component
        if component.tag == 'activity-alias':
            target_name = qualified(component.get(ANDROID+'targetActivity'))
            if target_name not in activities:
                raise ValueError('Alias target must be an earlier declared activity: '+target_name)
            target = activities[target_name]
        for intent in component.findall('intent-filter'):
            actions = {a.get(ANDROID+'name') for a in intent.findall('action')}
            categories = {c.get(ANDROID+'name') for c in intent.findall('category')}
            if 'android.intent.action.MAIN' not in actions:
                continue
            if 'android.intent.category.HOME' in categories:
                raise ValueError('Game must not register as a HOME screen replacement')
            if 'android.intent.category.LAUNCHER' not in categories:
                continue
            if component.get(ANDROID+'exported') != 'true':
                raise ValueError('Launcher must be explicitly exported')
            if component.get(ANDROID+'enabled', 'true') != 'true' or target.get(ANDROID+'enabled', 'true') != 'true':
                raise ValueError('Launcher or its target is disabled/unresolved')
            if component.get(ANDROID+'permission') or (component.tag == 'activity' and app.get(ANDROID+'permission')):
                raise ValueError('Launcher requires an external permission')
            launchers.append(name)
    if len(launchers) != 1:
        raise ValueError('Expected one enabled exported MAIN/LAUNCHER entry')
    return launchers[0]
