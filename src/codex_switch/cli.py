from __future__ import annotations

import argparse
from typing import Sequence

from codex_switch.config import AppPaths
from codex_switch.controller import AccountController
from codex_switch.repository import AccountRepository
import codex_switch.ui as ui_mod


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-switch", description="Codex account switcher")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("import", help="Import legacy ~/.codex/accounts/*.json snapshots")
    return parser


def run_import(paths: AppPaths) -> list[str]:
    repository = AccountRepository(paths)
    return repository.import_legacy_accounts(paths.legacy_accounts_dir)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    paths = AppPaths.detect()

    if args.command == "import":
        imported = run_import(paths)
        if imported:
            print("\n".join(imported))
        return 0

    controller = AccountController(paths)

    ui_mod.CodexSwitchApp(controller).run()
    return 0
