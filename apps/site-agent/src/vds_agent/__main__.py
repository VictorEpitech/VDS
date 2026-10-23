"""Point d'entrée : `python -m vds_agent` (aucun port entrant)."""

import asyncio
import logging
import sys

from .runtime import Agent
from .settings import AgentSettings, SettingsError
from .store import Store


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = AgentSettings.from_env()
    except SettingsError as e:
        print(f"vds-agent: {e}", file=sys.stderr)
        return 2
    store = Store(settings.data_dir, settings.state_key)
    if args[:1] == ["state-reset"] and len(args) >= 2:
        # Efface l'état d'un module (ex. jeton Samsung périmé, empreinte TLS changée).
        asyncio.run(store.state_delete(args[1], args[2] if len(args) > 2 else None))
        return 0
    asyncio.run(Agent(settings, store).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
