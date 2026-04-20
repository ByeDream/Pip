import argparse
import logging
import sys

from pip_agent.config import ConfigError, settings


def _configure_logging() -> None:
    """Route internal log records to stdout.

    Without this, Python's default WARNING threshold silently drops every
    ``log.info`` in the codebase — which hides scheduler ticks, heartbeat
    sentinel suppression, session ids, SDK cost/turn summaries, MCP tool
    calls, and the reflect pipeline. The only feedback users get is the
    agent's own text output, which is useless when the agent legitimately
    stays quiet (e.g. a ``HEARTBEAT_OK`` that was correctly silenced).

    We keep chatty third-party libraries at WARNING so the stream stays
    readable; bump them temporarily if you're debugging them specifically.
    """
    level = logging.INFO if settings.verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    for noisy in ("mcp", "anyio", "asyncio", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pip-boy")
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument(
        "--mode", choices=["auto", "cli", "scan"],
        default="auto",
        help="Channel mode: auto (connect all available), cli (CLI only), scan (force WeChat QR)",
    )
    parser.add_argument("--bind", default=None, help="Bind WeChat channel to a specific agent ID")
    args = parser.parse_args(argv)

    if args.version:
        from pip_agent import __version__
        print(f"pip-boy {__version__}")
        return

    _configure_logging()

    try:
        from pip_agent.agent_host import run_host
        run_host(mode=args.mode, bind_agent=args.bind)
    except ConfigError as exc:
        print(f"  [config error] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
