from __future__ import annotations

import logging
import sys

from zendesk_exporter.config import ExportConfig
from zendesk_exporter.exporter import KnowledgeBaseExporter
from zendesk_exporter.zendesk_client import ZendeskClient


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        config = ExportConfig.from_environment()
        client = ZendeskClient(config)
        exporter = KnowledgeBaseExporter(config=config, client=client)
        return exporter.run()
    except KeyboardInterrupt:
        logging.error("Export cancelled.")
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level CLI error boundary
        logging.exception("Export failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
