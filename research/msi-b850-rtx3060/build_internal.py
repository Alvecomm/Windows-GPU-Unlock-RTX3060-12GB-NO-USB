"""Build (never install) internal-ESP EFI images for the MSI B850 / RTX 3060 experiment.

Two images, both bound to ONE separate dedicated ESP by partition GUID instead of a
USB stick, and both handing off to Windows Boot Manager on the existing Windows ESP:

  probe   the existing generic READ-ONLY probe (-DGU_PROBE_GENERIC) without
          GU_USB_PROBE. Records PCI/console/driver data and PROM for profile
          generation; no driver release, GPU write, DMA or firmware-variable write.
  unlock  the existing headless LOCAL child + wrapper (entryLatchedSs1Full,
          automatic, headless) without GU_USB_MEDIA. Refused unless the generated
          LOCAL profile passes msi_b850_gate.py AND the repository's own LOCAL
          host-test evidence gate (Assert-LocalProfileEvidence) passes before and
          after compilation, with the pinned LLVM 20.1.8 toolchain.

The C sources and compiler flags are the repository's own (build.ps1,
build-unlock.ps1, build-probe.ps1); this script only drops the USB media define
and supplies the partition header through a clang VFS overlay, so nothing under
internal-boot/ is written. Output goes to research/msi-b850-rtx3060/build/.

This script never copies files to any ESP, never edits BCD/NVRAM/boot order and
never runs anything. --synthetic-header is a host regression mode: zero GUIDs, no
evidence, any clang, output named SYNTHETIC-NOT-FOR-EXECUTION and never a package.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
UNLOCK = REPO / 'internal-boot/unlock'
BOOT = REPO / 'internal-boot/boot'
EDK2 = REPO / 'deps/edk2/MdePkg/Include'
PINNED_LLVM = '20.1.8'

sys.path.insert(0, str(HERE))
import msi_b850_gate as gate  # noqa: E402

# Mirrors internal-boot/unlock/build.ps1 (child) and research/headless/build-unlock.ps1 /
# research/profile/build-probe.ps1 (wrappers). test_build_internal.py checks the parity.
COMMON = ['-target', 'x86_64-pc-windows-msvc', '-std=c11', '-ffreestanding', '-fno-builtin',
          '-fno-stack-protector', '-mno-red-zone', '-fshort-wchar', '-Os', '-Wall', '-Wextra', '-Werror']
CHILD_SOURCES = ['app', 'app_io', 'ownership', 'format', 'runtime', 'sha256', 'checks', 'port', 'compat',
                 'vendor/unlock_610', 'vendor/prep_610', 'vendor/layout_610', 'vendor/vbios_610', 'vendor/gfw_eval']
CHILD_DEFINES = ['-DGU_INTERNAL_BOOT', '-DGU_AUTOMATIC_BOOT', '-DGU_HEADLESS_RESEARCH',
                 '-DGU_EXPERIMENTAL_3090', '-DGU_ENTRY_LATCHED_SS1_FULL']
WRAPPER_SOURCES = ['handoff.c', 'full-release.c', 'full-flow.c', 'diagnostic.c',
                   '../unlock/checks.c', '../unlock/sha256.c', '../efi_runtime.c']
WRAPPER_DEFINES = ['-DGU_HEADLESS_RESEARCH', '-DGU_SINGLE_RTX_CONSOLE', '-DGU_FULL_UNLOCK', '-DGU_AUTOMATIC_BOOT']
PROBE_SOURCES = ['handoff.c', 'diagnostic.c', '../efi_runtime.c', 'probe.c', 'probe-parent.c', '../unlock/sha256.c',
                 '../../research/pci-decode/efi-capture.c', '../../research/pci-decode/snapshot.c']
PROBE_DEFINES = ['-DGU_DIAGNOSTIC_ONLY', '-DGU_DIAGNOSTIC_PROBE', '-DGU_PROBE_GENERIC',
                 '-DGU_PROBE_PARENT_BASELINE', '-DGU_PROBE_AUTO_BUS']
FORBIDDEN = {'-DGU_USB_MEDIA', '-DGU_USB_PROBE'}
LINK = ['/nologo', '/nodefaultlib', '/subsystem:efi_application', '/entry:efi_main', '/machine:x64', '/base:0',
        '/dynamicbase', '/nxcompat', '/timestamp:0', '/opt:ref', '/opt:icf']


class BuildRefused(Exception):
    pass


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def guid_initializer(value):
    b = uuid.UUID(str(value)).bytes_le
    tail = ','.join(f'0x{x:02x}' for x in b[8:16])
    return '{0x%08x,0x%04x,0x%04x,{%s}}' % (int.from_bytes(b[0:4], 'little'), int.from_bytes(b[4:6], 'little'),
                                             int.from_bytes(b[6:8], 'little'), tail)


def check_guids(internal, windows, synthetic):
    try:
        i, w = uuid.UUID(str(internal)), uuid.UUID(str(windows))
    except ValueError:
        raise BuildRefused('partition GUIDs must be GUIDs') from None
    if synthetic:
        return i, w
    if i.int == 0 or w.int == 0 or i == w:
        raise BuildRefused('the dedicated ESP and the Windows ESP need two different non-zero partition GUIDs')
    return i, w


def tool_version(tool):
    try:
        done = subprocess.run([tool, '--version'], capture_output=True, text=True, timeout=60)
    except OSError as error:
        raise BuildRefused(f'{tool}: {error}') from None
    return (done.stdout + done.stderr).strip().splitlines()[0] if (done.stdout + done.stderr).strip() else ''


def check_toolchain(clang, lld, synthetic):
    versions = {'clang': tool_version(clang), 'lld-link': tool_version(lld)}
    if not synthetic:
        for name, text in versions.items():
            if not re.search(r'\b' + re.escape(PINNED_LLVM) + r'\b', text):
                raise BuildRefused(f'{name} must be LLVM {PINNED_LLVM} for a real build; found {text!r}')
    return versions


def powershell_evidence(powershell):
    """Run the repository's own LOCAL evidence gate; return (tree sha, tested utc, evidence file sha)."""
    script = ("$ErrorActionPreference='Stop';. '" + str(UNLOCK / 'local-profile.ps1').replace("'", "''") + "';"
              "$e=Assert-LocalProfileEvidence '" + str(REPO).replace("'", "''") + "';"
              "[ordered]@{TreeSha256=[string]$e.TreeSha256;TestedUtc=[string]$e.TestedUtc} | ConvertTo-Json -Compress")
    try:
        done = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command', script],
                              capture_output=True, text=True, timeout=600)
    except OSError as error:
        raise BuildRefused(f'cannot run the LOCAL evidence gate: {error}') from None
    if done.returncode != 0:
        raise BuildRefused('LOCAL host-test evidence gate refused: ' + (done.stderr or done.stdout).strip()[-800:])
    try:
        value = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise BuildRefused('LOCAL evidence gate returned no result') from None
    return value['TreeSha256'], value['TestedUtc'], sha256(gate.LOCAL_EVIDENCE)


def write_overlay(out, header, platform, contract):
    roots = [{'type': 'file', 'name': str(BOOT / 'platform-config.h'), 'external-contents': str(platform),
              'use-external-name': False},
             {'type': 'file', 'name': str(BOOT / 'build/headless/child-contract.h'), 'external-contents': str(contract),
              'use-external-name': False}]
    if header is not None:
        roots.append({'type': 'file', 'name': str(UNLOCK / 'profile-local.h'), 'external-contents': str(header),
                      'use-external-name': False})
    overlay = out / 'overlay.json'
    overlay.write_text(json.dumps({'version': 0, 'roots': roots}, indent=1))
    return overlay


def compile_link(clang, lld, cwd, sources, flags, folder, name):
    folder.mkdir(parents=True)
    if FORBIDDEN & set(flags):
        raise BuildRefused('USB media defines are not allowed in an internal build')
    objects = []
    for index, source in enumerate(sources):
        obj = folder / f'{index:02d}-{Path(source).stem}.obj'
        done = subprocess.run([clang, *COMMON, *flags, '-c', source, '-o', str(obj)], cwd=cwd,
                              capture_output=True, text=True)
        (folder / f'{obj.stem}.log').write_text(done.stdout + done.stderr)
        if done.returncode:
            raise BuildRefused(f'compile failed: {source}\n' + (done.stdout + done.stderr)[-3000:])
        objects.append(str(obj))
    efi, map_file = folder / (name + '.EFI'), folder / (name + '.map')
    done = subprocess.run([lld, *LINK, '/map:' + str(map_file), '/out:' + str(efi), *objects],
                          capture_output=True, text=True)
    (folder / 'link.log').write_text(done.stdout + done.stderr)
    if done.returncode:
        raise BuildRefused('link failed: ' + (done.stdout + done.stderr)[-2000:])
    spec = importlib.util.spec_from_file_location('gu_verify_pe', UNLOCK / 'verify_pe.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    result = verifier.inspect(efi, map_file)
    (folder / 'pe-verification.json').write_text(json.dumps(result, indent=2))
    return efi, result


def build(args):
    synthetic = args.synthetic_header is not None
    internal, windows = check_guids(args.internal_esp_guid, args.windows_esp_guid, synthetic)
    versions = check_toolchain(args.clang, args.lld_link, synthetic)
    out = (args.output or (HERE / 'build' / (('synthetic-' if synthetic else '') + args.mode))).resolve()
    if out.exists():
        raise BuildRefused(f'output {out} exists; choose a new directory (nothing is overwritten)')

    header = report = None
    evidence = None
    if args.mode == 'unlock':
        target, proven = gate.load_target(), gate.load_json(gate.PROVEN)
        header = Path(args.synthetic_header).resolve() if synthetic else gate.LOCAL_HEADER
        if not header.is_file():
            raise BuildRefused('no generated LOCAL profile; capture with the read-only probe and run make-profile.py first')
        if synthetic:
            report = {'HeaderSha256': sha256(header), 'HardwareTested': False}
            gate.parse_header(header.read_text(encoding='ascii'))
        else:
            report = gate.load_json(gate.LOCAL_REPORT)
            gate.check_profile(header.read_bytes(), report, target, proven)
            evidence = powershell_evidence(args.powershell)
        header_sha = sha256(header)

    out.mkdir(parents=True)
    if synthetic:
        (out / 'NOT-A-PACKAGE.txt').write_text('Synthetic compile regression only. Not evidence, not a package. '
                                               'Never stage or execute these files.\n')
    platform = out / 'platform-config.h'
    platform.write_text('/* Internal dedicated-ESP configuration for the MSI B850 experiment; never distribute. */\n'
                        f'#define GU_INTERNAL_PARTITION_INITIALIZER {guid_initializer(internal)}\n'
                        f'#define GU_WINDOWS_PARTITION_INITIALIZER {guid_initializer(windows)}\n', newline='\n')
    contract = out / 'child-contract.h'
    contract.write_text('#error child contract is bound after the child is built\n')
    overlay = write_overlay(out, header if synthetic else None, platform, contract)
    inc = ['-I', str(EDK2), '-I', str(EDK2 / 'X64'), '-ivfsoverlay', str(overlay)]
    stem = 'SYNTHETIC-NOT-FOR-EXECUTION' if synthetic else None
    manifest = {'Format': 1, 'Target': 'msi-b850-gaming-plus-wifi6e-rtx3060-ga106', 'Mode': None,
                'HardwareTested': False, 'Synthetic': synthetic, 'Installed': False,
                'Installer': 'none: staging and boot-entry registration are manual, owner-approved steps',
                'Toolchain': versions, 'Builder': {'Path': 'research/msi-b850-rtx3060/build_internal.py',
                                                   'Sha256': sha256(__file__)},
                'PlatformConfigSha256': sha256(platform)}

    if args.mode == 'probe':
        efi, pe = compile_link(args.clang, args.lld_link, BOOT, PROBE_SOURCES, PROBE_DEFINES + inc,
                               out / 'probe', stem or 'PROBE-INTERNAL')
        manifest.update(Mode='generic-read-only-probe-then-windows-internal-esp', CanUnlock=False,
                        CanDetach=False, WritesFirmwareVariables=False, Defines=PROBE_DEFINES,
                        Efi={'File': efi.name, 'Sha256': pe['sha256'], 'Bytes': efi.stat().st_size},
                        Sources={s: sha256(BOOT / s) for s in PROBE_SOURCES})
    else:
        child_flags = ['-Wno-unused-parameter', '-D__KERNEL__', '-DGU_UEFI_PORT', '-DGU_TARGET_PCI_ID=0x250410deU',
                       *CHILD_DEFINES, '-DGU_PROFILE_LOCAL', '-I', 'compat', *inc]
        child, child_pe = compile_link(args.clang, args.lld_link, UNLOCK, [s + '.c' for s in CHILD_SOURCES],
                                       child_flags, out / 'child', stem or 'UNLOCK-HEADLESS-INTERNAL')
        contract.write_text(f'#define GU_UNLOCK_SHA256 "{child_pe["sha256"]}"\n#define GU_UNLOCK_BYTES {child.stat().st_size}\n',
                            newline='\n')
        wrapper, wrapper_pe = compile_link(args.clang, args.lld_link, BOOT, WRAPPER_SOURCES,
                                           [*WRAPPER_DEFINES, '-DGU_PROFILE_LOCAL', *inc], out / 'wrapper',
                                           stem or 'HEADLESS-INTERNAL')
        if not synthetic:
            after = powershell_evidence(args.powershell)
            if after != evidence or sha256(header) != header_sha:
                raise BuildRefused('LOCAL profile or evidence changed while building; rerun tests and rebuild')
        manifest.update(Mode='msi-b850-rtx3060-internal-headless-local-EXPERIMENTAL', CanUnlock=True,
                        RequiresHumanKey=False, ChildDefines=CHILD_DEFINES + ['-DGU_PROFILE_LOCAL'],
                        WrapperDefines=WRAPPER_DEFINES + ['-DGU_PROFILE_LOCAL'],
                        LocalProfileHeaderSha256=header_sha,
                        LocalEvidence=None if synthetic else {'TreeSha256': evidence[0], 'TestedUtc': evidence[1],
                                                              'FileSha256': evidence[2]},
                        Child={'File': child.name, 'Sha256': child_pe['sha256'], 'Bytes': child.stat().st_size},
                        Wrapper={'File': wrapper.name, 'Sha256': wrapper_pe['sha256'], 'Bytes': wrapper.stat().st_size},
                        ChildContractSha256=sha256(contract),
                        Sources={**{'unlock/' + s + '.c': sha256(UNLOCK / (s + '.c')) for s in CHILD_SOURCES},
                                 **{'boot/' + s: sha256(BOOT / s) for s in WRAPPER_SOURCES}})
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', newline='\n')
    return out, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('probe', 'unlock'))
    parser.add_argument('--internal-esp-guid', required=True, help='partition GUID of the separate dedicated ESP')
    parser.add_argument('--windows-esp-guid', required=True, help='partition GUID of the existing Windows ESP')
    parser.add_argument('--clang', default='clang')
    parser.add_argument('--lld-link', default='lld-link')
    parser.add_argument('--powershell', default='powershell.exe' if sys.platform == 'win32' else 'pwsh')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--synthetic-header', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        out, manifest = build(args)
    except (BuildRefused, gate.Refused, gate.Unusable) as error:
        print(f'Build refused: {error}', file=sys.stderr)
        return 3
    print(json.dumps({'output': str(out), 'mode': manifest['Mode'], 'synthetic': manifest['Synthetic'],
                      'hardware_tested': False, 'installed': False}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
