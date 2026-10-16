"""Summarize actual exported voice events; never invent missing timings."""
import argparse
import json
import math
from pathlib import Path


def percentiles(samples):
    values = sorted(samples)
    if not values:
        return {'samples': 0, 'p50_ms': None, 'p95_ms': None}
    def quantile(fraction):
        position = (len(values) - 1) * fraction
        low = int(position)
        high = min(low + 1, len(values) - 1)
        return round(values[low] + (values[high] - values[low]) * (position - low), 1)
    return {'samples': len(values), 'p50_ms': quantile(.5), 'p95_ms': quantile(.95)}


def summarize(trace):
    events = trace.get('events')
    if not isinstance(events, list):
        raise ValueError('Expected an exported voice trace with an events array.')
    samples = {'sdk_e2e_ms': [], 'server_response_ms': [], 'server_interruption_ms': []}
    seen_messages = set()
    def add(field, value, multiplier=1):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            samples[field].append(value * multiplier)
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'latency':
            for field in ['server_response_ms', 'server_interruption_ms']:
                add(field, event.get(field))
        if event.get('type') == 'sdk_metrics':
            message = (event.get('session_id'), event.get('message_id'))
            if message[1] and message in seen_messages:
                continue
            metrics = event.get('metrics', {})
            if isinstance(metrics, dict):
                add('sdk_e2e_ms', metrics.get('e2e_latency'), 1000)
            if message[1]:
                seen_messages.add(message)
    return {field: percentiles(values) for field, values in samples.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('trace', type=Path, nargs='+', help='JSON traces downloaded from the browser')
    args = parser.parse_args()
    combined = {'events': []}
    for path in args.trace:
        trace = json.loads(path.read_text())
        if not isinstance(trace, dict) or not isinstance(trace.get('events'), list):
            parser.error(f'{path.name}: expected an events array')
        combined['events'].extend(trace['events'])
    print(json.dumps(summarize(combined), indent=2))


if __name__ == '__main__':
    main()
