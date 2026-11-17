#!/usr/bin/env python3
"""Temporary Gen2 build-agent-only output-read permission extension.

The unchanged C policy is still the authority for all other paths.
This only allows a single broker-created diagnostic file pattern in scratch.
"""
RESULT_READ_PATTERNS = (
    '/srv/hhl-task2/agent/scratch/result-*/result.txt',
    'srv/hhl-task2/agent/scratch/result-*/result.txt',
    'scratch/result-*/result.txt',
)

def extended_read(base):
    if not isinstance(base, dict) or base.get('*') != 'deny':
        raise ValueError('invalid base policy')
    # Keep the env denies after every allow; OpenCode uses last-match-wins.
    if base.get('*.env') != 'deny' or base.get('*.env.*') != 'deny':
        raise ValueError('missing secret denies')
    result = {k:v for k,v in base.items() if k not in ('*.env','*.env.*')}
    if any(k in result for k in RESULT_READ_PATTERNS):
        raise ValueError('result pattern unexpectedly present in C policy')
    for p in RESULT_READ_PATTERNS:
        result[p] = 'allow'
    result['*.env']='deny'
    result['*.env.*']='deny'
    return result
