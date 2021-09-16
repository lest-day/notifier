import logging
from typing import List

import requests
import tomlkit
from tomlkit.exceptions import TOMLKitError

from notifier.database.drivers.base import BaseDatabaseDriver
from notifier.database.utils import try_cache
from notifier.types import (
    GlobalOverridesConfig,
    LocalConfig,
    SupportedWikiConfig,
)
from notifier.wikiconnection import Connection

logger = logging.getLogger(__name__)

# For ease of parsing, configurations are coerced to TOML format
wiki_config_listpages_body = '''
id = "%%form_data{id}%%"
name = """%%title%%""" # Vulnerability if titles are editable publicly
secure = %%form_data{secure}%%
'''


def get_global_config(
    local_config: LocalConfig,
    database: BaseDatabaseDriver,
    connection: Connection,
) -> None:
    """Retrieve remote global config for overrides and wikis."""
    try_cache(
        get=lambda: fetch_global_overrides(local_config),
        store=database.store_global_overrides,
        do_not_store={},
    )
    try_cache(
        get=lambda: fetch_supported_wikis(local_config, connection),
        store=database.store_supported_wikis,
        do_not_store=[],
    )


def fetch_global_overrides(local_config: LocalConfig) -> GlobalOverridesConfig:
    """Get the list of global override actions from the configuration
    wiki."""
    raw_config = requests.get(local_config["overrides_url"]).text
    config = {}
    try:
        config = parse_raw_overrides_config(raw_config)
    except (TOMLKitError, AssertionError) as error:
        logger.error("Couldn't parse global overrides config", exc_info=error)
    return config


def parse_raw_overrides_config(raw_config: str) -> GlobalOverridesConfig:
    """Parses a raw overrides config to lists of override objects sorted by
    the wiki ID they correspond to."""
    config = tomlkit.parse(raw_config)
    assert isinstance(config, dict)
    for overrides in config.values():
        assert isinstance(overrides, list)
        for override in overrides:
            assert "action" in override
    return config


def fetch_supported_wikis(
    local_config: LocalConfig, connection: Connection
) -> List[SupportedWikiConfig]:
    """Fetch the list of supported wikis from the configuration wiki."""
    configs = []
    for config_soup in connection.listpages(
        local_config["config_wiki"],
        category=local_config["wiki_config_category"],
        module_body=wiki_config_listpages_body,
    ):
        raw_config = config_soup.get_text()
        try:
            configs.append(parse_raw_wiki_config(raw_config))
        except (TOMLKitError, AssertionError) as error:
            logger.error(
                "Could not parse wiki config %s",
                {
                    "raw_config": raw_config,
                    "first_line": next(filter(bool, raw_config.split("\n"))),
                },
                exc_info=error,
            )
            continue
    return configs


def parse_raw_wiki_config(raw_config: str) -> SupportedWikiConfig:
    """Parses a raw wiki config to a suitable format."""
    config = tomlkit.parse(raw_config)
    assert isinstance(config, dict)
    assert "id" in config
    assert "secure" in config
    assert config["secure"] in (0, 1)
    return config