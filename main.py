from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from zendesk_confluence_migrator.config import AppConfig
from zendesk_confluence_migrator.confluence_client import ConfluenceClient
from zendesk_confluence_migrator.confluence_uploader import ConfluenceUploader
from zendesk_confluence_migrator.exporter import KnowledgeBaseExporter
from zendesk_confluence_migrator.models import MigrationManifest
from zendesk_confluence_migrator.zendesk_client import ZendeskClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a Zendesk Help Center category and migrate it into Confluence Cloud."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "export",
        help="Read Zendesk, download all content/assets, create a manifest and HTML ZIP.",
    )
    subparsers.add_parser(
        "preflight",
        help="Read-only Confluence validation. Creates no Confluence pages.",
    )
    upload = subparsers.add_parser(
        "upload",
        help="Create/update the migration pages in the configured existing Confluence space.",
    )
    upload.add_argument(
        "--yes",
        action="store_true",
        help="Required acknowledgement that this command writes to Confluence.",
    )
    return parser


def _find_workspace(config: AppConfig) -> tuple[Path, MigrationManifest]:
    output_dir = config.export.output_dir.resolve()
    if not output_dir.exists():
        raise RuntimeError("No export output exists. Run `python main.py export` first.")

    matches: list[tuple[Path, MigrationManifest]] = []
    for manifest_path in output_dir.glob("*/manifest.json"):
        try:
            manifest = MigrationManifest.load(manifest_path)
        except Exception:  # noqa: BLE001 - ignore unrelated/corrupt exports while locating target
            continue
        if (
            manifest.category_id == config.zendesk.category_id
            and manifest.locale == config.zendesk.locale
            and manifest.zendesk_host == config.zendesk.host
        ):
            matches.append((manifest_path.parent, manifest))

    if not matches:
        raise RuntimeError(
            "No matching manifest was found for ZENDESK_CATEGORY_URL. Run "
            "`python main.py export` first."
        )
    if len(matches) > 1:
        paths = "\n".join(f"  - {workspace}" for workspace, _ in matches)
        raise RuntimeError(
            "More than one matching export workspace was found. Remove/archive the stale one(s):\n"
            f"{paths}"
        )
    return matches[0]


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        if args.command == "export":
            config = AppConfig.from_environment(require_confluence=False, require_zendesk_auth=True)
            zendesk = ZendeskClient(config.zendesk, config.export)
            workspace = KnowledgeBaseExporter(config=config, client=zendesk).run()
            print(f"\nWorkspace: {workspace}")
            return 0

        config = AppConfig.from_environment(require_confluence=True, require_zendesk_auth=False)
        assert config.confluence is not None
        workspace, manifest = _find_workspace(config)
        confluence = ConfluenceClient(config.confluence, config.export)
        uploader = ConfluenceUploader(
            config=config,
            client=confluence,
            manifest=manifest,
            workspace_dir=workspace,
        )

        if args.command == "preflight":
            plan = uploader.preflight()
            return 0 if plan.valid else 2

        if args.command == "upload":
            if not args.yes:
                parser.error(
                    "upload writes pages/attachments to Confluence. Run `python main.py upload --yes` "
                    "after a successful preflight."
                )
            uploader.upload()
            return 0

        parser.error(f"Unknown command: {args.command}")
        return 2
    except KeyboardInterrupt:
        logging.error("Cancelled.")
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level CLI error boundary
        logging.exception("Command failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
