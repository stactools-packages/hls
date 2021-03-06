import stactools.core
from stactools.cli.registry import Registry

from stactools.hls.stac import create_collection, create_item

__all__ = ["create_item", "create_collection"]

stactools.core.use_fsspec()


def register_plugin(registry: Registry) -> None:
    from stactools.hls import commands

    registry.register_subcommand(commands.create_hls_command)


__version__ = "0.1.0"
