"""Compare compile-time Focus Filter metadata without an account or system changes."""
import json
from pathlib import Path
import subprocess
import tempfile


def run(*args):
    result = subprocess.run([str(arg) for arg in args], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        escaped = result.stdout[-10000:].replace('%', '%25').replace('\n', '%0A').replace('\r', '%0D')
        print(f'::error title=Focus metadata probe failed::{escaped}', flush=True)
        raise RuntimeError('Native metadata probe failed')
    return result.stdout.strip()


def notice(title, value):
    escaped = json.dumps(value, ensure_ascii=False).replace('%', '%25').replace('\n', '%0A').replace('\r', '%0D')
    print(f'::notice title={title}::{escaped}', flush=True)


def main():
    source = (Path(__file__).resolve().parents[1] / 'App/FocusAutomation.swift').read_text()
    developer = run('xcode-select', '-p')
    sdk = run('xcrun', '--show-sdk-path')
    xcode = run('xcodebuild', '-version').splitlines()[-1].split()[-1]
    variants = {
        'baseline': source,
        'explicit-configuration': source.replace('    var displayRepresentation:', '''    init() { useFilter = false }

    static var parameterSummary: some ParameterSummary {
        Summary("Доставка TGFocus: \\(\\.$useFilter)")
    }

    var displayRepresentation:'''),
    }
    for label, text in variants.items():
        with tempfile.TemporaryDirectory(prefix='tgfocus-intent-probe-') as directory:
            root = Path(directory)
            (root / 'Focus.swift').write_text(text)
            (root / 'Main.swift').write_text('@main struct Probe { static func main() {} }')
            sources = [root / 'Focus.swift', root / 'Main.swift']
            (root / 'protocols.json').write_text('["AppIntent", "SetFocusFilterIntent"]')
            run('swiftc', '-module-name', 'TelegramFocus', '-swift-version', '5',
                '-target', 'arm64-apple-macos14.0', '-O', '-whole-module-optimization',
                '-emit-const-values-path', root / 'const.swiftconstvalues',
                '-Xfrontend', '-const-gather-protocols-file', '-Xfrontend', root / 'protocols.json',
                '-framework', 'AppKit', '-framework', 'AppIntents', *sources, '-o', root / 'Probe')
            (root / 'sources.list').write_text('\n'.join(map(str, sources)))
            (root / 'const.list').write_text(str(root / 'const.swiftconstvalues'))
            (root / 'empty.list').touch()
            run('xcrun', 'appintentsmetadataprocessor', '--toolchain-dir',
                Path(developer) / 'Toolchains/XcodeDefault.xctoolchain', '--module-name', 'TelegramFocus',
                '--sdk-root', sdk, '--xcode-version', xcode, '--platform-family', 'macOS',
                '--deployment-target', '14.0', '--bundle-identifier', 'local.mxlev.TelegramFocus',
                '--output', root / 'output', '--target-triple', 'arm64-apple-macos14.0',
                '--binary-file', root / 'Probe', '--source-file-list', root / 'sources.list',
                '--swift-const-vals-list', root / 'const.list', '--metadata-file-list', root / 'empty.list',
                '--static-metadata-file-list', root / 'empty.list', '--compile-time-extraction',
                '--deployment-aware-processing', '--validate-assistant-intents', '--no-app-shortcuts-localization')
            data = json.loads((root / 'output/Metadata.appintents/extract.actionsdata').read_text())
            notice(f'Focus metadata {label}', data['actions']['TGFocusFilter'])


if __name__ == '__main__':
    main()
