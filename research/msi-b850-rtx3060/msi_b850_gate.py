"""Offline identity gate for the MSI B850 / RTX 3060 (GA106) internal-boot experiment.

Reads files only. It never touches hardware, partitions, EFI files, BCD or
firmware settings, and a pass never authorizes an unlock by itself:

  inventory  compare a tools/collect_compatibility.py JSON report with target.json
  profile    check that a generated LOCAL profile (profile-local.h + profile.json)
             describes this exact card family/identity and stays inside proven.json
  readiness  list which build gates are present and which evidence is still missing

Exit codes: 0 pass, 3 refused (mismatch), 2 unusable input.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TARGET = HERE / 'target.json'
PROVEN = REPO / 'research/profile/proven.json'
LOCAL_HEADER = REPO / 'internal-boot/unlock/profile-local.h'
LOCAL_REPORT = REPO / 'research/profile/local/profile.json'
LOCAL_EVIDENCE = REPO / 'research/profile/local/test-results.json'
HEX64 = re.compile(r'[0-9a-f]{64}')


class Refused(Exception):
    """The input is well formed but describes something other than the target."""


class Unusable(Exception):
    """The input cannot be parsed or is incomplete."""


def decode_text(data):
    # Windows PowerShell 5.1 '>' writes UTF-16 LE with a BOM; accept that and UTF-8 (BOM optional).
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    return data.decode('utf-8-sig')


def load_json(path):
    try:
        return json.loads(decode_text(Path(path).read_bytes()))
    except (OSError, UnicodeError, ValueError) as error:
        raise Unusable(f'{path}: {error}') from None


def load_target(path=TARGET):
    target = load_json(path)
    if target.get('format') != 1 or target.get('status') != 'experimental-unvalidated':
        raise Unusable('target.json has an unexpected format or status')
    return target


# ---------------------------------------------------------------- inventory

def _pci(row):
    pci = row.get('pci') if isinstance(row, dict) else None
    if not isinstance(pci, dict):
        return None
    return tuple(pci.get(k) for k in ('vendor', 'device', 'subsystem_vendor', 'subsystem_device'))


def check_inventory(report, target):
    """Return a list of matched facts; raise Refused on the first mismatch."""
    if not isinstance(report, dict) or report.get('schema_version') != 1:
        raise Unusable('not a schema_version 1 collector report')
    if report.get('collection_complete') is not True or report.get('missing_sections'):
        raise Unusable('collector report is incomplete; rerun tools/collect_compatibility.py')
    hw = report.get('hardware')
    if not isinstance(hw, dict):
        raise Unusable('collector report has no hardware section')
    want = target['reported_by_owner']
    matched = []

    boards = hw.get('board') or []
    if len(boards) != 1 or not isinstance(boards[0], dict) or not isinstance(boards[0].get('Product'), str):
        raise Unusable('expected exactly one baseboard row')
    product = boards[0]['Product']
    if want['board_product_contains'] not in product:
        raise Refused(f'baseboard {product!r} is not {want["board_product_contains"]}')
    ids = re.findall(r'MS-[0-9A-Z]{4}', product)
    if ids and ids != [want['board_id']]:
        raise Refused(f'baseboard id {ids} is not {want["board_id"]}')
    matched.append(f'baseboard {product}')

    bioses = hw.get('bios') or []
    if len(bioses) != 1 or not isinstance(bioses[0], dict):
        raise Unusable('expected exactly one BIOS row')
    version = bioses[0].get('SMBIOSBIOSVersion')
    if version != want['bios_version']:
        raise Refused(f'BIOS version {version!r} is not {want["bios_version"]}; '
                      'any BIOS change invalidates every capture and profile')
    matched.append(f'BIOS {version}')

    cpus = hw.get('cpu') or []
    if len(cpus) != 1 or want['cpu_name_contains'] not in str(cpus[0].get('Name')):
        raise Refused('CPU is not ' + want['cpu_name_contains'])
    matched.append('CPU ' + want['cpu_name_contains'])

    video = hw.get('video') or []
    rtx = tuple(want['rtx'][k] for k in ('vendor', 'device', 'subsystem_vendor', 'subsystem_device'))
    igpu = tuple(want['igpu'][k] for k in ('vendor', 'device', 'subsystem_vendor', 'subsystem_device'))
    nvidia = [_pci(r) for r in video if _pci(r) and _pci(r)[0] == '10de']
    if nvidia != [rtx]:
        raise Refused(f'NVIDIA display devices {nvidia} are not exactly one {":".join(rtx)}')
    matched.append('RTX %s:%s subsystem %s:%s' % rtx)
    others = [_pci(r) for r in video if _pci(r) and _pci(r)[0] != '10de']
    if others not in ([], [igpu]):
        raise Refused(f'other display devices {others} differ from the reported iGPU {":".join(igpu)}')
    matched.append('iGPU ' + ('absent from Windows inventory' if not others else '%s:%s subsystem %s:%s' % igpu))
    return matched


# ---------------------------------------------------------------- profile

def parse_header(text):
    """Return {name: value} for '#define NAME VALUE' rows; refuse duplicates and #undef."""
    if re.search(r'(?m)^[ \t]*#[ \t]*undef\b', text):
        raise Refused('generated header must not contain #undef')
    defines = {}
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r'[ \t]*#[ \t]*define[ \t]+([A-Z0-9_]+)\b[ \t]*(.*)$', line)
        i += 1
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        while value.endswith('\\') and i < len(lines):
            value = value[:-1] + lines[i]
            i += 1
        if name in defines:
            raise Refused(f'{name} is defined more than once')
        defines[name] = ' '.join(value.split())
    return defines


def _ga106_nvgop(proven):
    for chip in proven.get('chips', []):
        if chip.get('family') == 'GA106':
            return chip
    raise Unusable('proven.json has no GA106 entry')


def check_profile(header_bytes, report, target, proven):
    """Check a generated LOCAL profile against the target identity and the proven envelope."""
    try:
        text = header_bytes.decode('ascii')
    except UnicodeError:
        raise Unusable('profile-local.h is not ASCII') from None
    d = parse_header(text)
    derived = target['derived_from_owner_values']
    traits = target['repository_reviewed_traits']
    facts = []

    if not re.fullmatch(r'[0-9]+', d.get('GU_LOCAL_PROFILE_GENERATOR', '')):
        raise Refused('profile-local.h was not produced by research/profile/make-profile.py')
    for name, expected in (('GU_BOARD_ID', derived['gu_board_id']),
                           ('GU_BOARD_SUBSYSTEM', derived['gu_board_subsystem']),
                           ('GU_CHIP_ID', traits['chip_id']),
                           ('GU_DEVICE_MEMORY_SIZE', traits['device_memory_mib_hex'])):
        if d.get(name) != expected:
            raise Refused(f'{name} is {d.get(name)!r}, expected {expected} for this card')
        facts.append(f'{name} {expected}')

    segment = d.get('GU_TARGET_SEGMENT', '0x0U')
    if segment not in ('0x0U', '0U'):
        raise Refused(f'PCI segment {segment} is outside this experiment (segment 0 only)')
    bus = d.get('GU_TARGET_BUS', '1U')
    if not re.fullmatch(r'(0x[0-9a-f]{1,2}|[0-9]{1,3})U', bus) or not 1 <= int(bus[:-1], 0) <= 255:
        raise Refused(f'GU_TARGET_BUS {bus!r} is invalid')
    facts.append(f'PCI segment 0 bus {bus} (captured, not assumed)')

    prom = d.get('GU_PROM_SHA256', '')
    if not re.fullmatch(r'"[0-9a-f]{64}"', prom):
        raise Refused('GU_PROM_SHA256 is missing or malformed')
    for name in ('GU_BAR0_BASE', 'GU_BAR0_END', 'GU_RTX_PATH_BYTES', 'GU_ROOT_PORT_DEVICE',
                 'GU_ROOT_PORT_FUNCTION', 'GU_NO_EDID_DRIVER'):
        if name not in d:
            raise Refused(f'{name} is missing; regenerate from current probe captures')

    chip = _ga106_nvgop(proven)
    allowed = {(int(x['version'], 16), x['text_sha256']) for x in chip['nvgop']}
    try:
        version = int(d.get('GU_NVGOP_VERSION', '').rstrip('U'), 16)
    except ValueError:
        raise Refused('GU_NVGOP_VERSION is missing or malformed') from None
    code = d.get('GU_NVGOP_TEXT_SHA256', '').strip('"')
    if (version, code) not in allowed:
        raise Refused(f'nvgop {version:#x} .text {code[:16]}... has never been released on GA106 hardware '
                      '(research/profile/proven.json); widening that needs reviewed hardware evidence')
    facts.append(f'nvgop {version:#x} matches the reviewed GA106 entry')

    if not isinstance(report, dict):
        raise Unusable('profile.json is not an object')
    if report.get('HeaderSha256') != hashlib.sha256(header_bytes).hexdigest():
        raise Refused('profile.json does not describe these exact header bytes')
    if report.get('HardwareTested') is not False:
        raise Refused('profile.json must be an untested generated report')
    replay = report.get('Replay') or {}
    if replay.get('RtxId') != derived['gu_board_id'][2:10]:
        raise Refused('profile.json RtxId differs from the header')
    if replay.get('IgpuId') not in (None, derived['igpu_capture_id']):
        raise Refused(f'captured integrated GPU {replay.get("IgpuId")} is not the reported AMD 13c0')
    if report.get('PromSha256') != prom.strip('"'):
        raise Refused('profile.json PROM hash differs from the header')
    if not isinstance(report.get('Captures'), list) or len(report['Captures']) < proven.get('min_captures', 2):
        raise Refused('profile.json lists fewer captures than proven.json requires')
    facts.append('iGPU in captures: ' + (replay.get('IgpuId') or 'none recorded'))
    facts.append('console: ' + ('aggregate' if replay.get('Aggregate') else 'direct')
                 + f', {replay.get("ChildCount")} RTX display child(ren), EDID driver '
                 + ('absent' if replay.get('NoEdidDriver') else 'present'))
    return facts


# ---------------------------------------------------------------- readiness

def readiness(target, proven, esp_config=None):
    """Report gates without deciding anything that needs hardware."""
    gates = []

    def gate(name, ok, detail):
        gates.append({'gate': name, 'satisfied': bool(ok), 'detail': detail})

    header_ok = LOCAL_HEADER.is_file() and LOCAL_REPORT.is_file()
    detail = 'no generated LOCAL profile yet (needs two read-only probe captures)'
    if header_ok:
        try:
            facts = check_profile(LOCAL_HEADER.read_bytes(), load_json(LOCAL_REPORT), target, proven)
            detail = '; '.join(facts)
        except (Refused, Unusable) as error:
            header_ok, detail = False, str(error)
    gate('generated LOCAL profile matches this machine', header_ok, detail)
    gate('host-test evidence file present', LOCAL_EVIDENCE.is_file(),
         'research/profile/test-profile.ps1 must pass on Windows; the build re-verifies it exactly')
    if esp_config:
        try:
            cfg = load_json(esp_config)
            ok = (cfg.get('Format') == 1 and cfg.get('SeparateEsp') is True
                  and cfg.get('UnlockPartitionGuid') != cfg.get('WindowsPartitionGuid')
                  and (cfg.get('WindowsEsp') or {}).get('HasBootManager') is True
                  and cfg.get('SecureBootEnabled') is False)
            gate('separate dedicated ESP inspected', ok, str(esp_config))
        except Unusable as error:
            gate('separate dedicated ESP inspected', False, str(error))
    else:
        gate('separate dedicated ESP inspected', False, 'run inspect-esp.ps1 on Windows (read-only)')
    gate('physical validation', False, 'never satisfied by software; requires a supervised boot and logs')
    return {'target': target['id'], 'can_unlock': False, 'gates': gates}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('inventory');p.add_argument('report', type=Path)
    p = sub.add_parser('profile');p.add_argument('--header', type=Path, default=LOCAL_HEADER)
    p.add_argument('--report', type=Path, default=LOCAL_REPORT)
    p = sub.add_parser('readiness');p.add_argument('--esp-config', type=Path)
    for p in sub.choices.values():
        p.add_argument('--target', type=Path, default=TARGET)
        p.add_argument('--proven', type=Path, default=PROVEN)
    args = parser.parse_args(argv)
    try:
        target = load_target(args.target)
        proven = load_json(args.proven)
        if args.command == 'inventory':
            result = {'result': 'match', 'facts': check_inventory(load_json(args.report), target)}
        elif args.command == 'profile':
            try:
                header = args.header.read_bytes()
            except OSError as error:
                raise Unusable(str(error)) from None
            result = {'result': 'match', 'facts': check_profile(header, load_json(args.report), target, proven)}
        else:
            result = readiness(target, proven, args.esp_config)
    except Refused as error:
        print(json.dumps({'result': 'refused', 'reason': str(error)}, indent=2))
        return 3
    except Unusable as error:
        print(json.dumps({'result': 'unusable-input', 'reason': str(error)}, indent=2))
        return 2
    result['can_unlock'] = False
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
