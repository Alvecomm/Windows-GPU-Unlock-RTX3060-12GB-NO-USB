"""Host regression for the MSI B850 / RTX 3060 internal-boot experiment.

No hardware, ESP, BCD or firmware access. Every profile/inventory value here is
synthetic; a pass is software evidence only and never hardware compatibility.

  python research/msi-b850-rtx3060/test_msi_b850.py [--clang clang] [--lld-link lld-link] [--host-clang clang]

On Linux the host-compiled C test needs a compiler that gives UEFI-size wchar_t
(the default adds -fshort-wchar); on Windows run it from an MSVC developer prompt.
"""
import argparse
import contextlib
import copy
import io
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import msi_b850_gate as gate  # noqa: E402
import build_internal as builder  # noqa: E402

FIXTURE = HERE / 'tests/fixtures/synthetic-ga106-direct-noedid.h'
OPTIONS = {'clang': 'clang', 'lld_link': 'lld-link', 'host_clang': 'clang'}
GA106_NVGOP = ('0x60010U', '615b37a708b1894e18d7fa60cfcdacaa4d8b598863a10b8103d231a595847b2b')
GUID_A = '6a3f3c1e-8a21-4c47-9a53-1b0c2d3e4f50'  # synthetic
GUID_B = '0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0'  # synthetic


def inventory(**changes):
    report = {
        'schema_version': 1, 'purpose': 'compatibility-research-inventory', 'collection_complete': True,
        'missing_sections': [],
        'hardware': {
            'board': [{'Manufacturer': 'Micro-Star International Co., Ltd.', 'Product': 'B850 GAMING PLUS WIFI6E (MS-7E80)'}],
            'cpu': [{'Name': 'AMD Ryzen 7 9700X 8-Core Processor', 'Manufacturer': 'AuthenticAMD'}],
            'os': [{'Caption': 'Microsoft Windows 11 Pro', 'Version': '10.0.26300', 'BuildNumber': '26300', 'OSArchitecture': '64-bit'}],
            'bios': [{'Manufacturer': 'American Megatrends International, LLC.', 'SMBIOSBIOSVersion': '1.A20'}],
            'video': [
                {'Name': 'NVIDIA GeForce RTX 3060', 'DriverVersion': 'x', 'pci_status': 'parsed',
                 'pci': {'vendor': '10de', 'device': '2504', 'subsystem_vendor': '1462', 'subsystem_device': '397d'}},
                {'Name': 'AMD Radeon(TM) Graphics', 'DriverVersion': 'x', 'pci_status': 'parsed',
                 'pci': {'vendor': '1002', 'device': '13c0', 'subsystem_vendor': '1462', 'subsystem_device': 'ee80'}},
            ],
        },
        'assessment': {'status': 'not-assessed', 'can_unlock': False},
    }
    for path, value in changes.items():
        node = report
        keys = path.split('.')
        for key in keys[:-1]:
            node = node[int(key)] if isinstance(node, list) else node[key]
        last = keys[-1]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
    return report


def header(**changes):
    values = {
        'GU_LOCAL_PROFILE_GENERATOR': '3', 'GU_PROFILE_NAME': '"LOCAL-250410de-397d1462-00000000"',
        'GU_BOARD_ID': '0x250410deU', 'GU_BOARD_SUBSYSTEM': '0x397d1462U', 'GU_TARGET_SEGMENT': '0x0U',
        'GU_TARGET_BUS': '0x1U', 'GU_NO_EDID_DRIVER': '1', 'GU_LOCAL_CONSOLE_AGGREGATE': '0',
        'GU_LOCAL_CHILD_COUNT': '1', 'GU_BAR0_BASE': '0x10000000ULL', 'GU_BAR0_END': '0x10ffffffULL',
        'GU_DEVICE_MEMORY_SIZE': '0x3000U', 'GU_CHIP_ID': '0xb76000a1U', 'GU_PROM_SHA256': '"' + '1' * 64 + '"',
        'GU_ROOT_PORT_DEVICE': '1U', 'GU_ROOT_PORT_FUNCTION': '1U', 'GU_RTX_PATH_BYTES': '2,1,12,0',
        'GU_NVGOP_VERSION': GA106_NVGOP[0], 'GU_NVGOP_TEXT_SHA256': '"' + GA106_NVGOP[1] + '"',
    }
    extra = changes.pop('_extra', '')
    for key, value in changes.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    text = '/* synthetic test header */\n' + ''.join(f'#define {k} {v}\n' for k, v in values.items()) + extra
    return text.encode('ascii')


def report_for(data, **changes):
    report = {'HeaderSha256': hashlib.sha256(data).hexdigest(), 'HardwareTested': False, 'PromSha256': '1' * 64,
              'Captures': [{'name': 'DIAG00.TXT'}, {'name': 'DIAG01.TXT'}],
              'Replay': {'RtxId': '250410de', 'IgpuId': '13c01002', 'Aggregate': False, 'ChildCount': 1,
                         'NoEdidDriver': True}}
    for key, value in changes.items():
        if key.startswith('Replay.'):
            report['Replay'][key[7:]] = value
        else:
            report[key] = value
    return report


class InventoryGate(unittest.TestCase):
    def setUp(self):
        self.target = gate.load_target()

    def test_owner_reported_machine_matches(self):
        facts = gate.check_inventory(inventory(), self.target)
        self.assertIn('BIOS 1.A20', facts)

    def test_igpu_absent_from_windows_is_allowed(self):
        report = inventory()
        report['hardware']['video'].pop(1)
        gate.check_inventory(report, self.target)

    def test_refusals(self):
        nv = 'hardware.video.0.pci'
        cases = {
            'other BIOS': {'hardware.bios.0.SMBIOSBIOSVersion': '1.A10'},
            'other board': {'hardware.board.0.Product': 'TUF GAMING B650M-PLUS WIFI'},
            'other MSI board id': {'hardware.board.0.Product': 'B850 GAMING PLUS WIFI6E (MS-7E81)'},
            'other CPU': {'hardware.cpu.0.Name': 'AMD Ryzen 7 9800X3D'},
            'other card subsystem': {nv: {'vendor': '10de', 'device': '2504', 'subsystem_vendor': '1458', 'subsystem_device': '4096'}},
            'GA104 RTX 3060': {nv: {'vendor': '10de', 'device': '2487', 'subsystem_vendor': '1462', 'subsystem_device': '397d'}},
            'RTX 3090': {nv: {'vendor': '10de', 'device': '2204', 'subsystem_vendor': '10de', 'subsystem_device': '1454'}},
            'other iGPU': {'hardware.video.1.pci': {'vendor': '1002', 'device': '164e', 'subsystem_vendor': '1043', 'subsystem_device': '8877'}},
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(gate.Refused):
                gate.check_inventory(inventory(**change), self.target)
        report = inventory()
        report['hardware']['video'].append(copy.deepcopy(report['hardware']['video'][0]))
        with self.assertRaises(gate.Refused):
            gate.check_inventory(report, self.target)

    def test_incomplete_is_unusable(self):
        with self.assertRaises(gate.Unusable):
            gate.check_inventory(inventory(collection_complete=False), self.target)
        with self.assertRaises(gate.Unusable):
            gate.check_inventory({'schema_version': 2}, self.target)


    def test_powershell_redirect_encodings_are_read(self):
        text = json.dumps(inventory())
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in {
                'utf-16 bom (PowerShell 5.1 >)': b'\xff\xfe' + text.encode('utf-16-le'),
                'utf-8 bom': b'\xef\xbb\xbf' + text.encode('utf-8'),
                'utf-8': text.encode('utf-8'),
            }.items():
                with self.subTest(name):
                    path = Path(tmp) / 'inventory.json'
                    path.write_bytes(data)
                    gate.check_inventory(gate.load_json(path), self.target)
            path = Path(tmp) / 'bad.json'
            path.write_bytes(b'\xff\x00garbage')
            with self.assertRaises(gate.Unusable):
                gate.load_json(path)


class EspConfigGate(unittest.TestCase):
    GOOD = {'Format': 1, 'SeparateEsp': True, 'UnlockPartitionGuid': '{55555555-0000-0000-0000-000000000000}',
            'WindowsPartitionGuid': '{66666666-0000-0000-0000-000000000000}',
            'WindowsEsp': {'HasBootManager': True}, 'SecureBootEnabled': False}

    def esp_gate(self, cfg, raw=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'esp-config.json'
            path.write_bytes(raw if raw is not None else json.dumps(cfg).encode())
            result = gate.readiness(gate.load_target(), gate.load_json(gate.PROVEN), path)
        self.assertFalse(result['can_unlock'])
        return next(g for g in result['gates'] if 'ESP' in g['gate'])['satisfied']

    def test_sample_layout_passes_and_bom_is_read(self):
        self.assertTrue(self.esp_gate(self.GOOD))
        self.assertTrue(self.esp_gate(None, b'\xef\xbb\xbf' + json.dumps(self.GOOD).encode()))

    def test_refusals(self):
        for name, change in {
            'not separate': {'SeparateEsp': False},
            'same partition': {'UnlockPartitionGuid': self.GOOD['WindowsPartitionGuid']},
            'boot manager unknown': {'WindowsEsp': {'HasBootManager': None}},
            'secure boot unknown': {'SecureBootEnabled': None},
            'secure boot on': {'SecureBootEnabled': True},
        }.items():
            with self.subTest(name):
                self.assertFalse(self.esp_gate({**self.GOOD, **change}))


class ProfileGate(unittest.TestCase):
    def setUp(self):
        self.target, self.proven = gate.load_target(), gate.load_json(gate.PROVEN)

    def check(self, data, report=None):
        return gate.check_profile(data, report if report is not None else report_for(data), self.target, self.proven)

    def test_matching_synthetic_profile_passes(self):
        facts = self.check(header())
        self.assertIn('nvgop 0x60010 matches the reviewed GA106 entry', facts)

    def test_existing_reviewed_profiles_are_not_substitutes(self):
        asus = header(GU_BOARD_ID='0x220410deU', GU_BOARD_SUBSYSTEM='0x145410deU', GU_CHIP_ID='0xb72000a1U',
                      GU_DEVICE_MEMORY_SIZE='0x6000U', GU_NVGOP_VERSION='0x60008U',
                      GU_NVGOP_TEXT_SHA256='"6aad26e6909c9791dd0caba92d6658e9dfd8009aee80676002bf24ce2ad24d08"')
        h610m = header(GU_BOARD_SUBSYSTEM='0x40961458U')
        b660plus = header(GU_BOARD_ID='0x248710deU', GU_BOARD_SUBSYSTEM='0x153010deU', GU_CHIP_ID='0xb74000a1U')
        for name, data in (('ASUS B650M 3090 FE', asus), ('Gigabyte H610M 3060', h610m), ('B660-PLUS GA104 3060', b660plus)):
            with self.subTest(name), self.assertRaises(gate.Refused):
                self.check(data)

    def test_refusals(self):
        cases = {
            'GA102 chip': header(GU_CHIP_ID='0xb72000a1U'),
            '8 GiB memory': header(GU_DEVICE_MEMORY_SIZE='0x2000U'),
            'unreviewed nvgop version': header(GU_NVGOP_VERSION='0x60011U'),
            'nvgop from GA102 only list': header(GU_NVGOP_VERSION='0x60008U',
                                                 GU_NVGOP_TEXT_SHA256='"6aad26e6909c9791dd0caba92d6658e9dfd8009aee80676002bf24ce2ad24d08"'),
            'different nvgop code': header(GU_NVGOP_TEXT_SHA256='"' + '2' * 64 + '"'),
            'nonzero segment': header(GU_TARGET_SEGMENT='0x1U'),
            'bad bus': header(GU_TARGET_BUS='0x0U'),
            'missing PROM pin': header(GU_PROM_SHA256=None),
            'missing BAR pin': header(GU_BAR0_BASE=None),
            'missing EDID flag': header(GU_NO_EDID_DRIVER=None),
            'hand-written header': header(GU_LOCAL_PROFILE_GENERATOR=None),
            'duplicate define': header(_extra='#define GU_BOARD_ID 0x250410deU\n'),
            'undef': header(_extra='#undef GU_CHIP_ID\n'),
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(gate.Refused):
                self.check(data)

    def test_report_binding(self):
        data = header()
        for name, report in {
            'other header bytes': report_for(header(GU_TARGET_BUS='0x2U')),
            'claims hardware tested': report_for(data, HardwareTested=True),
            'Intel iGPU captured': report_for(data, **{'Replay.IgpuId': '46828086'}),
            'other RTX id': report_for(data, **{'Replay.RtxId': '248710de'}),
            'PROM differs': report_for(data, PromSha256='3' * 64),
            'one capture': report_for(data, Captures=[{'name': 'DIAG00.TXT'}]),
        }.items():
            with self.subTest(name), self.assertRaises(gate.Refused):
                self.check(data, report)
        self.check(data, report_for(data, **{'Replay.IgpuId': None}))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            tmp = Path(tmp)
            good = tmp / 'good.h'
            good.write_bytes(header())
            (tmp / 'good.json').write_text(json.dumps(report_for(header())))
            self.assertEqual(gate.main(['profile', '--header', str(good), '--report', str(tmp / 'good.json')]), 0)
            bad = tmp / 'bad.h'
            bad.write_bytes(header(GU_BOARD_SUBSYSTEM='0x40961458U'))
            (tmp / 'bad.json').write_text(json.dumps(report_for(bad.read_bytes())))
            self.assertEqual(gate.main(['profile', '--header', str(bad), '--report', str(tmp / 'bad.json')]), 3)
            self.assertEqual(gate.main(['profile', '--header', str(tmp / 'none.h'), '--report', str(tmp / 'good.json')]), 2)
            (tmp / 'inv.json').write_text(json.dumps(inventory()))
            self.assertEqual(gate.main(['inventory', str(tmp / 'inv.json')]), 0)

    def test_readiness_never_claims_unlock(self):
        result = gate.readiness(self.target, self.proven)
        self.assertFalse(result['can_unlock'])
        self.assertFalse([g for g in result['gates'] if g['gate'] == 'physical validation'][0]['satisfied'])


def ps_flags(path):
    return set(re.findall(r'-D[A-Z0-9_]+', (REPO / path).read_text()))


class BuilderPolicy(unittest.TestCase):
    def args(self, mode='unlock', **kw):
        ns = argparse.Namespace(mode=mode, internal_esp_guid=GUID_A, windows_esp_guid=GUID_B,
                                clang=OPTIONS['clang'], lld_link=OPTIONS['lld_link'], powershell='pwsh',
                                output=None, synthetic_header=None)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_flag_parity_with_repository_scripts(self):
        """Same defines as the repository scripts, minus only the USB media selectors."""
        build_ps1 = ps_flags('internal-boot/unlock/build.ps1')
        for define in builder.CHILD_DEFINES + ['-DGU_PROFILE_LOCAL', '-DGU_UEFI_PORT', '-D__KERNEL__']:
            self.assertTrue(define in build_ps1 or define == '-DGU_PROFILE_LOCAL', define)
        unlock_ps1 = ps_flags('research/headless/build-unlock.ps1')
        self.assertTrue(set(builder.WRAPPER_DEFINES) <= unlock_ps1)
        probe_ps1 = ps_flags('research/profile/build-probe.ps1')
        self.assertTrue(set(builder.PROBE_DEFINES) <= probe_ps1)
        self.assertEqual(probe_ps1 - set(builder.PROBE_DEFINES), {'-DGU_USB_PROBE', '-DGU_PROBE_BUS'})
        usb = (REPO / 'research/headless/build-usb.ps1').read_text()
        for source in builder.WRAPPER_SOURCES:
            self.assertIn("'" + source + "'", usb)
        for source in builder.CHILD_SOURCES:
            self.assertIn("'" + source + "'", (REPO / 'internal-boot/unlock/build.ps1').read_text())
        for source in builder.PROBE_SOURCES:
            self.assertIn("'" + source + "'", (REPO / 'research/profile/build-probe.ps1').read_text())
        for flag in builder.COMMON + builder.LINK:
            self.assertIn(flag, (REPO / 'research/headless/build-usb.ps1').read_text())
        self.assertFalse(builder.FORBIDDEN & set(builder.CHILD_DEFINES + builder.WRAPPER_DEFINES + builder.PROBE_DEFINES))

    def test_partition_guids(self):
        zero = '00000000-0000-0000-0000-000000000000'
        for internal, windows in ((zero, GUID_B), (GUID_A, zero), (GUID_A, GUID_A), ('not-a-guid', GUID_B)):
            with self.subTest(internal=internal, windows=windows), self.assertRaises(builder.BuildRefused):
                builder.check_guids(internal, windows, False)
        builder.check_guids(GUID_A, GUID_B, False)

    def test_real_build_requires_pinned_llvm(self):
        version = builder.tool_version(OPTIONS['clang'])
        if builder.PINNED_LLVM in version:
            self.skipTest('pinned toolchain present; refusal path not observable')
        with self.assertRaises(builder.BuildRefused):
            builder.check_toolchain(OPTIONS['clang'], OPTIONS['lld_link'], False)

    def test_real_unlock_build_refused_without_local_profile(self):
        if gate.LOCAL_HEADER.exists():
            self.skipTest('a real local profile exists in this checkout; not touching it')
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            original = builder.check_toolchain
            builder.check_toolchain = lambda *a: {'clang': 'test', 'lld-link': 'test'}
            try:
                with self.assertRaises(builder.BuildRefused):
                    builder.build(self.args(output=out))
            finally:
                builder.check_toolchain = original
            self.assertFalse(out.exists())

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(builder.BuildRefused):
                builder.build(self.args(mode='probe', output=Path(tmp), synthetic_header=FIXTURE))


class ReadOnlyScripts(unittest.TestCase):
    MUTATING = r'\b(Set-Partition|New-Partition|Remove-Partition|Resize-Partition|Format-Volume|Initialize-Disk|' \
               r'Add-PartitionAccessPath|Remove-PartitionAccessPath|Set-Disk|bcdedit|bcdboot|mountvol|diskpart|' \
               r'Disable-BitLocker|Suspend-BitLocker|Enable-BitLocker|Set-ItemProperty|New-ItemProperty|Remove-Item|' \
               r'Copy-Item|Move-Item|Set-SecureBootUEFI|Set-FirmwareEnvironmentVariable|Restart-Computer|Stop-Computer|shutdown)\b'

    def test_inspector_has_no_mutating_commands(self):
        text = (HERE / 'inspect-esp.ps1').read_text()
        code = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
        self.assertIsNone(re.search(self.MUTATING, code, re.IGNORECASE))
        writes = re.findall(r'\b(Set-Content|Out-File|WriteAll\w+|CreateDirectory)\b', code)
        self.assertEqual(sorted(writes), ['CreateDirectory', 'WriteAllText'])
        # the one file written is the report, as UTF-8 without a BOM
        self.assertIn('WriteAllText([IO.Path]::GetFullPath($Output)', code)
        self.assertIn('UTF8Encoding $false', code)

    def test_python_tools_have_no_install_paths(self):
        for name in ('build_internal.py', 'msi_b850_gate.py'):
            code = (HERE / name).read_text()
            self.assertIsNone(re.search(r'bcdedit|bcdboot|mountvol|diskpart|EFI\\\\Microsoft|ResetSystem', code), name)

    def test_inspector_parses(self):
        pwsh = shutil.which('pwsh') or shutil.which('powershell.exe')
        if not pwsh:
            self.skipTest('PowerShell not installed')
        script = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('"
                  + str(HERE / 'inspect-esp.ps1') + "',[ref]$t,[ref]$e);$e.Count")
        done = subprocess.run([pwsh, '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True)
        self.assertEqual(done.stdout.strip(), '0', done.stderr)


class SyntheticBuilds(unittest.TestCase):
    """Compile/link/PE regression only. Nothing is executed or staged."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='msi-b850-efi-'))
        zero = '00000000-0000-0000-0000-000000000000'
        base = dict(internal_esp_guid=zero, windows_esp_guid=zero, clang=OPTIONS['clang'],
                    lld_link=OPTIONS['lld_link'], powershell='pwsh', synthetic_header=FIXTURE)
        cls.unlock = builder.build(argparse.Namespace(mode='unlock', output=cls.tmp / 'unlock', **base))
        cls.probe = builder.build(argparse.Namespace(mode='probe', output=cls.tmp / 'probe', **base))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_unlock_images_are_marked_synthetic(self):
        out, manifest = self.unlock
        self.assertTrue((out / 'NOT-A-PACKAGE.txt').is_file())
        self.assertTrue(manifest['Synthetic'] and not manifest['HardwareTested'] and not manifest['Installed'])
        for part in ('Child', 'Wrapper'):
            self.assertEqual(manifest[part]['File'], 'SYNTHETIC-NOT-FOR-EXECUTION.EFI')
        contract = (out / 'child-contract.h').read_text()
        self.assertIn(manifest['Child']['Sha256'], contract)
        self.assertIn(f'GU_UNLOCK_BYTES {manifest["Child"]["Bytes"]}', contract)

    def test_probe_is_read_only_capability(self):
        _, manifest = self.probe
        self.assertFalse(manifest['CanUnlock'] or manifest['CanDetach'] or manifest['WritesFirmwareVariables'])
        self.assertNotIn('-DGU_USB_PROBE', manifest['Defines'])

    def test_internal_media_branch_is_compiled(self):
        """Preprocess the wrapper with the builder's flags: internal GUID lookup in, USB-only branch out."""
        out, _ = self.unlock
        flags = [*builder.COMMON, *builder.WRAPPER_DEFINES, '-DGU_PROFILE_LOCAL', '-I', str(builder.EDK2),
                 '-I', str(builder.EDK2 / 'X64'), '-ivfsoverlay', str(out / 'overlay.json'), '-E', '-P', 'handoff.c']
        text = subprocess.run([OPTIONS['clang'], *flags], cwd=builder.BOOT, capture_output=True, text=True, check=True).stdout
        prepare = text[text.index('static EFI_STATUS Prepare('):]
        prepare = prepare[:prepare.index('\n}')]
        self.assertIn('FindPartition(&GuInternalPartition,&own)', prepare.replace(' ', ''))
        self.assertNotIn('usb', prepare)
        usb = subprocess.run([OPTIONS['clang'], *flags, '-DGU_USB_MEDIA'], cwd=builder.BOOT,
                             capture_output=True, text=True, check=True).stdout
        self.assertIn('!usb', usb[usb.index('static EFI_STATUS Prepare('):].split('\n}')[0].replace(' ', ''))

    def test_repository_handoff_media_checks_with_distinct_guids(self):
        """Run the repository's own test_handoff.c with an MSI-style two-ESP configuration (synthetic GUIDs)."""
        out, _ = self.unlock
        work = self.tmp / 'handoff'
        work.mkdir()
        image = work / 'child.bin'
        image.write_bytes(b'synthetic child bytes for the hash/size contract\n' * 8)
        platform = work / 'platform-config.h'
        platform.write_text(f'#define GU_INTERNAL_PARTITION_INITIALIZER {builder.guid_initializer(GUID_A)}\n'
                            f'#define GU_WINDOWS_PARTITION_INITIALIZER {builder.guid_initializer(GUID_B)}\n')
        generated = work / 'generated.h'
        generated.write_text(f'#define GU_UNLOCK_SHA256 "{hashlib.sha256(image.read_bytes()).hexdigest()}"\n'
                             f'#define GU_UNLOCK_BYTES {image.stat().st_size}\n')
        overlay = work / 'overlay.json'
        overlay.write_text(json.dumps({'version': 0, 'roots': [
            {'type': 'file', 'name': str(builder.BOOT / 'platform-config.h'), 'external-contents': str(platform)},
            {'type': 'file', 'name': str(builder.BOOT / 'generated.h'), 'external-contents': str(generated)}]}))
        exe = work / ('test-handoff.exe' if sys.platform == 'win32' else 'test-handoff')
        flags = ['-std=c11', '-O1', '-Wall', '-Wextra', '-Werror', '-D_CRT_SECURE_NO_WARNINGS',
                 '-I', str(builder.EDK2), '-I', str(builder.EDK2 / 'X64'), '-ivfsoverlay', str(overlay)]
        if sys.platform != 'win32':
            flags.append('-fshort-wchar')
        done = subprocess.run([OPTIONS['host_clang'], *flags, 'test_handoff.c', 'flow.c', '../unlock/sha256.c', '-o', str(exe)],
                              cwd=builder.BOOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        run = subprocess.run([str(exe), str(image)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('failures=0', run.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--clang', default='clang')
    parser.add_argument('--lld-link', default='lld-link')
    parser.add_argument('--host-clang', default='clang')
    args, rest = parser.parse_known_args()
    OPTIONS.update(clang=args.clang, lld_link=args.lld_link, host_clang=args.host_clang)
    unittest.main(argv=[sys.argv[0], *rest], verbosity=2)


if __name__ == '__main__':
    main()
