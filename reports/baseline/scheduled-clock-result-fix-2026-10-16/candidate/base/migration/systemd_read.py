"""Lossless systemctl show reader; repeated commands and unordered dependencies."""
import re

COMMANDS = {'ExecStart', 'ExecStartPre', 'ExecStartPost', 'ExecStop', 'ExecStopPost',
            'ExecCondition', 'ExecReload'}
PROPERTIES = ('LoadState,ActiveState,MainPID,ControlPID,UnitFileState,Unit,User,'
              'ExecStart,ExecStartPre,ExecStartPost,ExecStop,ExecStopPost,ExecCondition,ExecReload,'
              'WorkingDirectory,After,Requires,DropInPaths,FragmentPath,NeedDaemonReload')

def parse(raw):
    result = {}
    for line in raw.splitlines():
        if not line:
            continue
        if '=' not in line:
            raise ValueError('Malformed systemctl property')
        key, value = line.split('=', 1)
        if key in result:
            if key not in COMMANDS:
                raise ValueError('Repeated scalar property: ' + key)
            result[key] += '\n' + value
        else:
            result[key] = value
    return result

def commands(value):
    if not value:
        return []
    found = re.findall(r'argv\[\]=(.*?) ;', value)
    if not found or len(found) != value.count('argv[]='):
        raise ValueError('Unrecognised command property')
    return found

def inactive(props):
    return props.get('ActiveState') in {'inactive', 'failed'} and all(
        props.get(key, '0') == '0' for key in ('MainPID', 'ControlPID'))

def verify(props, spec, *, quiescent=False):
    if props.get('LoadState') != 'loaded' or props.get('NeedDaemonReload') != 'no':
        raise ValueError('Unit missing or needs daemon-reload')
    if props.get('DropInPaths') or props.get('FragmentPath') != spec['fragment']:
        raise ValueError('Unexpected unit source/drop-in')
    for key in COMMANDS:
        if commands(props.get(key, '')) != spec.get(key, []):
            raise ValueError('Effective command differs: ' + key)
    for key in ('User', 'WorkingDirectory', 'Unit'):
        if key in spec and props.get(key) != spec[key]:
            raise ValueError('Effective property differs: ' + key)
    for key in ('Requires', 'After'):
        if not set(spec.get(key, [])).issubset(props.get(key, '').split()):
            raise ValueError('Missing dependency: ' + key)
    if quiescent and not inactive(props):
        raise ValueError('Business unit is not quiescent')

