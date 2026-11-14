"""One system-wide monotonic coordinate, shared explicitly with workload children.

Wall anchors are provenance only: never convert a timestamp through wall time.
The lineage token prevents accidental comparison with another run/clock domain.
"""
import math
import os
from pathlib import Path
import sys
import time
import uuid


def system_identity():
    if sys.platform.startswith('linux'):
        # A different boot or time namespace has a different monotonic coordinate.
        return {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'time_namespace': os.readlink('/proc/self/ns/time')}
    # Python >=3.10 supplies a system-wide monotonic clock on supported hosts.
    return {'host': os.uname().nodename, 'platform': sys.platform}


_DOMAIN = uuid.uuid4().hex
_SYSTEM = system_identity()


def now():
    return time.monotonic()


def metadata():
    before = time.time()
    mono = now()
    after = time.time()
    info = time.get_clock_info('monotonic')
    return dict(format='lab-monotonic-v2', domain_id=_DOMAIN, clock='time.monotonic',
                unit='seconds', system=_SYSTEM, implementation=info.implementation,
                monotonic=info.monotonic, adjustable=info.adjustable,
                anchor={'monotonic': mono, 'wall_before': before, 'wall_after': after})


def adopt(clock):
    """Spawn/fork children must use the parent's domain, on the same system."""
    global _DOMAIN
    if (clock.get('format') != 'lab-monotonic-v2' or clock.get('system') != system_identity()
            or clock.get('clock') != 'time.monotonic' or clock.get('unit') != 'seconds'
            or clock.get('implementation') != time.get_clock_info('monotonic').implementation
            or clock.get('monotonic') is not True or clock.get('adjustable') is not False
            or not clock.get('domain_id')):
        raise ValueError('foreign or unsupported monotonic clock domain')
    _DOMAIN = clock['domain_id']


def interval(begin, end):
    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (begin, end))
            or begin >= end):
        raise ValueError('invalid or empty monotonic interval')
    return dict(begin=begin, end=end, clock_domain=_DOMAIN)
