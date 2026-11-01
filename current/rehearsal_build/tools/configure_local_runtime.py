#!/usr/bin/env python3
"""Record the installed local inference runtime for this app, once.

The two filesystem paths are supplied explicitly — nothing is discovered by
guessing and nothing is copied: the app points at the runtime and weights that
are already installed on this machine. This tool writes only this app's own
owner-only configuration file; it never edits another application's
configuration, never starts a server and never downloads a model.

Usage::

    python3 tools/configure_local_runtime.py \\
        --runtime-executable /path/to/llama-server \\
        --model-weights /path/to/model.gguf

Then start the app with the local runtime::

    python3 run_mock.py --local-runtime --desktop

Omit ``--port`` to keep the default 9931, ``--alias`` for the default alias and
``--context`` for 8192. Run with no arguments to print the current setting.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai import local_config, local_runtime  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--runtime-executable',
                        help='absolute path to the installed llama-server binary')
    parser.add_argument('--model-weights',
                        help='absolute path to the installed .gguf weights file')
    parser.add_argument('--alias', default=local_runtime.DEFAULT_WIRE_MODEL,
                        help='server alias (default: %(default)s)')
    parser.add_argument('--context', type=int, default=local_runtime.DEFAULT_CONTEXT,
                        help='the -c context bound (default: %(default)s)')
    parser.add_argument('--port', type=int,
                        default=int(local_runtime.DEFAULT_BASE_URL.rsplit(':', 1)[1]),
                        help='loopback port (default: %(default)s)')
    args = parser.parse_args(argv)

    if not args.runtime_executable and not args.model_weights:
        try:
            current = local_config.read()
        except ValueError as exc:
            raise SystemExit('Stored local runtime configuration is invalid: %s' % exc)
        print(json.dumps(current or {'configured': False}, indent=2, sort_keys=True))
        return 0
    if not args.runtime_executable or not args.model_weights:
        parser.error('--runtime-executable and --model-weights must be given together')

    # Fail now, with the exact path, rather than at launch time.
    executable = Path(args.runtime_executable)
    weights = Path(args.model_weights)
    if not executable.is_file():
        raise SystemExit('No such file: %s' % executable)
    if not weights.is_file():
        raise SystemExit('No such file: %s' % weights)
    try:
        path = local_config.save({'runtime_executable': str(executable.resolve()),
                                  'model_weights': str(weights.resolve()),
                                  'alias': args.alias, 'context': args.context,
                                  'port': args.port})
    except ValueError as exc:
        raise SystemExit('Could not save local runtime configuration: %s' % exc)
    print('Recorded local runtime configuration in %s' % path)
    print('Start the app with: python3 run_mock.py --local-runtime --desktop')
    return 0


if __name__ == '__main__':
    sys.exit(main())
