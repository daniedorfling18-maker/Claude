from pathlib import Path

path = Path("src/polymarket_predictive_engine/cli.py")
text = path.read_text(encoding="utf-8")

if "from .snapshot_label_collector import collect_snapshot_labels" not in text:
    text = text.replace(
        "from .resolution_collector import collect_resolutions\n",
        "from .resolution_collector import collect_resolutions\n"
        "from .snapshot_label_collector import collect_snapshot_labels\n",
    )

if '    "collect-snapshot-labels",' not in text:
    text = text.replace(
        '    "resolve-websocket-markets",\n',
        '    "resolve-websocket-markets",\n'
        '    "collect-snapshot-labels",\n',
    )

if 'elif args.command == "collect-snapshot-labels":' not in text:
    text = text.replace(
        '        elif args.command == "resolve-websocket-markets":\n'
        '            _print(collect_websocket_resolutions(cfg))\n',
        '        elif args.command == "resolve-websocket-markets":\n'
        '            _print(collect_websocket_resolutions(cfg))\n'
        '        elif args.command == "collect-snapshot-labels":\n'
        '            _print(collect_snapshot_labels(cfg))\n',
    )

path.write_text(text, encoding="utf-8")
print("Patched cli.py")
