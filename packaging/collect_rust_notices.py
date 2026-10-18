"""Collect resolved native Cargo dependency notices without inventing licences.

Run cargo tree with --locked --edges normal,build --prefix none separately for
host MSVC and client GNU targets first, and from 1.0.0.7 for the Windows client's
transport (client/transport for x86_64-pc-windows-msvc, --windows-client-tree).
This includes build dependencies, not just code linked at runtime. Existing
notices are retained for older bundled components. Optional supplemental texts
must identify exact upstream VCS commits. --vendor adds a `cargo vendor` folder
(crates verified against Cargo.lock) as a source beside Cargo's registry cache.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tomllib

NOTICE_NAME = re.compile(r"(?i)^(licen[sc]e|notice|copying|copyright|authors)(?:$|[-_. ])")
TREE_LINE = re.compile(r"([^\s]+) v([^\s]+)(.*)")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def notice_paths(folder: Path):
    for file in sorted(folder.rglob('*')):
        if not file.is_file() or file.is_symlink():
            continue
        relative = file.relative_to(folder)
        if NOTICE_NAME.match(file.name) or any(part.lower() in ('license', 'licenses', 'licences') for part in relative.parts[:-1]):
            if b'\0' not in file.read_bytes():
                yield file


def package_roots(cargo_home: Path, upstream: Path, vendor: Path | None = None):
    registry = {}
    manifests = list((cargo_home / 'registry/src').glob('*/*/Cargo.toml'))
    if vendor is not None:
        manifests += sorted(vendor.glob('*/Cargo.toml'))
    for manifest in manifests:
        package = tomllib.loads(manifest.read_text())['package']
        registry[(package['name'], package['version'])] = manifest.parent
    git = {}
    for manifest in (cargo_home / 'git/checkouts').glob('*/*/**/Cargo.toml'):
        if any(part in ('target', '.git') for part in manifest.parts):
            continue
        package = tomllib.loads(manifest.read_text()).get('package', {})
        if isinstance(package.get('name'), str) and isinstance(package.get('version'), str):
            git[(package['name'], package['version'])] = manifest.parent
    workspace = tomllib.loads((upstream / 'Cargo.toml').read_text())['workspace']['package']
    local = {}
    for manifest in (upstream / 'alvr').glob('*/Cargo.toml'):
        package = tomllib.loads(manifest.read_text()).get('package', {})
        version = package.get('version')
        if isinstance(version, dict):
            version = workspace['version']
        local[(package['name'], version)] = manifest.parent
    return registry, git, local, workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cargo-home', type=Path, required=True)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--host-tree', type=Path, required=True)
    parser.add_argument('--client-tree', type=Path, required=True)
    parser.add_argument('--windows-client-tree', type=Path)
    parser.add_argument('--vendor', type=Path)
    parser.add_argument('--supplemental', type=Path)
    parser.add_argument('--rust-toolchain-docs', type=Path)
    parser.add_argument('--release', default='1.0.0.13')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / 'licenses/rust'
    output.mkdir(parents=True, exist_ok=True)
    prior = json.loads((output / 'INDEX.json').read_text()) if (output / 'INDEX.json').exists() else []
    entries = {(item['crate'], item['version']): dict(item) for item in prior}
    selected = {}
    graphs = {}
    trees = [('windows_host', 'x86_64-pc-windows-msvc', args.host_tree, args.upstream / 'Cargo.lock'),
             ('linux_client', 'x86_64-unknown-linux-gnu', args.client_tree, root / 'client/transport/Cargo.lock')]
    if args.windows_client_tree:
        trees.append(('windows_client', 'x86_64-pc-windows-msvc', args.windows_client_tree, root / 'client/transport/Cargo.lock'))
    for label, target, tree, lock in trees:
        locks = {(p['name'], p['version']): p for p in tomllib.loads(lock.read_text())['package']}
        graph_packages = set()
        for line in tree.read_text().splitlines():
            match = TREE_LINE.fullmatch(line)
            if not match:
                continue
            key = match[1], match[2]
            graph_packages.add(key)
            info = selected.setdefault(key, {'graphs': [], 'locks': {}})
            if label not in info['graphs']:
                info['graphs'].append(label)
            info['locks'][label] = locks.get(key, {})
        graphs[label] = {'target': target, 'root': 'alvr_server_openvr' if label == 'windows_host' else 'vrostbridge_transport',
                         'features': ['gpl'] if label == 'windows_host' else [], 'edges': ['normal', 'build'],
                         'packages': len(graph_packages), 'cargo_lock_sha256': digest(lock.read_bytes()),
                         'cargo_tree_sha256': digest(tree.read_bytes())}
    registry, git, local, workspace = package_roots(args.cargo_home, args.upstream, args.vendor)
    supplemental = {}
    if args.supplemental:
        supplemental = {(r['crate'], r['version']): r for r in json.loads((args.supplemental / 'INDEX.json').read_text())}
    missing_sources, missing_notices, local_project = [], [], []
    changed, added, reused = 0, 0, 0
    active = []
    for key, info in sorted(selected.items()):
        name, version = key
        folder = registry.get(key) or git.get(key) or local.get(key)
        if name == 'vrostbridge_transport':
            local_project.append({'crate': name, 'version': version, 'graphs': info['graphs']})
            continue
        if folder is None:
            missing_sources.append({'crate': name, 'version': version, 'graphs': info['graphs']})
            continue
        package = tomllib.loads((folder / 'Cargo.toml').read_text())['package']
        for field in ('license', 'authors', 'repository'):
            if isinstance(package.get(field), dict):
                package[field] = workspace.get(field)
        entry = {'crate': name, 'version': version, 'license': package.get('license'),
                 'repository': package.get('repository'), 'authors': package.get('authors', []),
                 'declared_license_file': package.get('license-file'),
                 'graphs': info['graphs'], 'notice_files': [], 'notice_details': [],
                 'source': next((p.get('source') for p in info['locks'].values() if p.get('source')), 'pinned ALVR workspace'),
                 'cargo_package_checksums': {g: p['checksum'] for g, p in info['locks'].items() if p.get('checksum')}}
        if key in registry:
            entry['source_url'] = f'https://crates.io/crates/{name}/{version}'
            notice_root = folder
            sources = list(notice_paths(folder))
        elif key in local:
            entry['source_url'] = 'https://github.com/alvr-org/ALVR/tree/f5023f8e78fa14ea57fb1038548d7820289a8524'
            notice_root = args.upstream
            sources = [args.upstream / 'LICENSE']
            entry['repository'] = 'https://github.com/alvr-org/ALVR'
        else:
            entry['source_url'] = entry['source']
            # Cargo git checkouts retain one shared repository licence at root.
            notice_root = folder
            while notice_root.parent != args.cargo_home / 'git/checkouts' and not (notice_root / '.git').exists():
                if list(notice_paths(notice_root)):
                    break
                notice_root = notice_root.parent
            sources = list(notice_paths(notice_root))
        license_file = package.get('license-file')
        if license_file:
            declared = (folder / license_file).resolve()
            if declared.is_file() and declared not in sources:
                sources.append(declared)
        vcs = folder / '.cargo_vcs_info.json'
        if vcs.exists():
            entry['cargo_vcs_info'] = json.loads(vcs.read_text())
        destination = output / f'{name}-{version}'
        destination.mkdir(exist_ok=True)
        for file in sources:
            relative = file.relative_to(notice_root) if file.is_relative_to(notice_root) else Path(file.name)
            data = file.read_bytes()
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() == data:
                reused += 1
            else:
                if target.exists(): changed += 1
                else: added += 1
                target.write_bytes(data)
            entry['notice_files'].append(relative.as_posix())
            entry['notice_details'].append({'file': relative.as_posix(), 'sha256': digest(data), 'bytes': len(data), 'origin': 'resolved Cargo package' if key in registry else 'upstream repository'})
        extra = supplemental.get(key)
        if extra:
            for notice in extra.get('notice_files', []):
                file = args.supplemental / f'{name}-{version}' / notice['path']
                data = file.read_bytes()
                if digest(data) != notice['sha256']:
                    raise ValueError(f'Supplemental notice checksum mismatch: {file}')
                if extra.get('commit') != entry.get('cargo_vcs_info', {}).get('git', {}).get('sha1'):
                    raise ValueError(f'Supplemental notice does not match crate VCS revision: {name}')
                authority = notice.get('origin') == 'license authority'
                if authority and notice.get('declared_license') != entry['license']:
                    raise ValueError(f'Authority licence text does not match package declaration: {name}')
                relative = Path('license-authority' if authority else 'upstream') / notice['path']
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.read_bytes() == data: reused += 1
                else:
                    if target.exists(): changed += 1
                    else: added += 1
                    target.write_bytes(data)
                entry['notice_files'].append(relative.as_posix())
                detail = {'file': relative.as_posix(), 'sha256': digest(data), 'bytes': len(data),
                          'origin': 'SPDX License List canonical text matching the package declaration' if authority else 'exact crate VCS revision',
                          'source_url': notice['url']}
                detail['declared_by_crate_commit' if authority else 'commit'] = extra['commit']
                entry['notice_details'].append(detail)
                if authority:
                    entry['upstream_notice_gap'] = 'No full licence file was bundled in the crate or its exact repository commit; the declared CC0-1.0 legal text is supplied from SPDX License List Data v3.27.0.'
        # Reuse previously verified exact-version supplements when collecting
        # again without a network download/cache of those licence files.
        previous = entries.get(key, {})
        if previous.get('source') == entry['source'] and previous.get('license') == entry['license']:
            for notice in previous.get('notice_details', []):
                if notice['file'] in entry['notice_files']:
                    continue
                target = destination / notice['file']
                if not target.is_file() or digest(target.read_bytes()) != notice['sha256']:
                    continue
                commit = entry.get('cargo_vcs_info', {}).get('git', {}).get('sha1')
                if notice.get('commit', notice.get('declared_by_crate_commit')) != commit:
                    continue
                entry['notice_files'].append(notice['file'])
                entry['notice_details'].append(notice)
                if previous.get('upstream_notice_gap'):
                    entry['upstream_notice_gap'] = previous['upstream_notice_gap']
                reused += 1
        if not entry['notice_files']:
            # Do not manufacture a copyright line from an SPDX identifier.
            entry['missing_notice'] = 'Package declares a licence but no full notice text was found in the cached package or verified upstream supplements.'
            missing_notices.append({'crate': name, 'version': version, 'license': entry['license'], 'repository': entry['repository']})
        entry['notice_files'] = sorted(set(entry['notice_files']))
        (destination / 'PACKAGE.json').write_text(json.dumps(entry, indent=2) + '\n')
        entries[key] = entry
        active.append(key)
    # Preserve existing dependency acknowledgments even when outside these
    # compile graphs; copied upstream DLLs and helper builds may still need them.
    for key, item in entries.items():
        if key not in active:
            item['coverage'] = f'retained from earlier bundled components; outside the current {len(graphs)} native Cargo graphs'
    toolchain = None
    if args.rust_toolchain_docs:
        folder = output / 'rust-standard-library-1.97.1'
        folder.mkdir(exist_ok=True)
        files = [args.rust_toolchain_docs / 'COPYRIGHT-library.html']
        files += sorted((args.rust_toolchain_docs / 'licenses').glob('*'))
        collected = []
        for file in files:
            if file.is_file():
                relative = file.relative_to(args.rust_toolchain_docs)
                target = folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
                collected.append({'file': relative.as_posix(), 'sha256': digest(file.read_bytes()), 'bytes': file.stat().st_size})
        toolchain = {'version': '1.97.1', 'source_url': 'https://static.rust-lang.org/dist/rust-std-1.97.1-x86_64-unknown-linux-gnu.tar.xz',
                     'scope': 'Official Rust standard-library copyright inventory plus its referenced licence texts; some texts cover components not necessarily linked by this application.', 'files': collected}
        (folder / 'INDEX.json').write_text(json.dumps(toolchain, indent=2) + '\n')
    (output / 'INDEX.json').write_text(json.dumps([entries[k] for k in sorted(entries)], indent=2) + '\n')
    report = {'release': args.release, 'scope': 'Cargo normal and build dependency graphs, not a claim that every listed crate is linked into the final executable.',
              'graphs': graphs, 'current_graph_packages': len(selected), 'current_third_party_packages': len(active),
              'retained_earlier_packages': len(entries) - len(active), 'index_entries': len(entries),
              'registry_packages': sum(k in registry for k in active), 'git_packages': sum(k in git for k in active),
              'alvr_workspace_packages': sum(k in local for k in active), 'local_project_packages': local_project,
              'notice_files_added': added, 'notice_files_reused': reused, 'notice_files_updated': changed,
              'missing_package_sources': missing_sources, 'missing_notice_texts': missing_notices,
              'upstream_notice_gaps_covered_by_license_authority': [{'crate': entries[k]['crate'], 'version': entries[k]['version'], 'note': entries[k]['upstream_notice_gap']} for k in active if entries[k].get('upstream_notice_gap')],
              'rust_standard_library': toolchain, 'index_sha256': digest((output / 'INDEX.json').read_bytes())}
    evidence = root / f'docs/evidence/rust-notices-{args.release}.json'
    evidence.write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# Rust dependency notices', '', report['scope'], '',
             'The index contains exact versions, declared licence expressions, source locations,',
             'Cargo archive checksums and SHA-256 checksums of the included notice texts.',
             'Existing notices for other bundled components are retained. The original licence',
             'terms apply to each component; an SPDX declaration is not a replacement licence.', '',
             f"Current native build graphs: {len(active)} third-party packages; {len(entries)-len(active)} earlier entries retained.",
             f"Full notice text unavailable: {len(missing_notices)} packages. See MISSING_NOTICES.json for explicit gaps.", '',
             '| Package | Version | Declared licence | Build graph |', '| --- | --- | --- | --- |']
    for key in active:
        e = entries[key]
        lines.append(f"| {e['crate']} | {e['version']} | {e['license'] or 'Not declared'} | {', '.join(e['graphs'])} |")
    (output / 'README.md').write_text('\n'.join(lines) + '\n')
    (output / 'MISSING_NOTICES.json').write_text(json.dumps(missing_notices, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('current_third_party_packages', 'registry_packages', 'git_packages', 'index_entries', 'missing_package_sources', 'missing_notice_texts')}))


if __name__ == '__main__':
    main()
