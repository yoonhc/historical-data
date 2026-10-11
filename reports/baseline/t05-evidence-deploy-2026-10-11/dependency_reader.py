"""Compare systemd dependency membership; preserve every other original check.

After/Requires are unordered unit dependencies, not execution command lists.
The original root-preserved reader and its activation receipt remain intact.
"""
import deploy_common as c

ORIGINAL_VERIFY = c.verify_service


def normalized_dependencies(values):
    result = dict(values)
    for key in ('After', 'Requires'):
        result[key] = [' '.join(sorted(c.one(values, key).split()))]
    return result


def verify_service(actual, previous, expected_command=None):
    ORIGINAL_VERIFY(normalized_dependencies(actual),
                    normalized_dependencies(previous), expected_command)


def install_reader():
    c.verify_service = verify_service
