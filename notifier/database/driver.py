import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from sqlite3.dbapi2 import Cursor
from typing import Any, Callable, Dict, Iterable, List, Tuple, Type, TypedDict

from notifier.config.tool import SupportedSiteConfig
from notifier.config.user import Subscription, UserConfig

sqlite3.enable_callback_tracebacks(True)


def try_cache(
    *,
    get: Callable,
    store: Callable,
    do_not_store: Any = None,
    catch: Tuple[Type[Exception], ...] = None,
) -> None:
    """Attempts to retrieve data from somewhere. If it succeeds, caches the
    data. If it fails, does nothing.

    Intended usage is for the data to be subsequently retrieved from the
    cache. This ensure that valid data is always received, even if the
    original call failed - in this case, old cached data is used instead.

    If it is necessary for the getter to succeed, this function should not
    be used.

    :param get: Callable that takes no argument that retrieves data, and
    may fail.

    :param store: Callable that takes the result of `get` as its only
    argument and caches the data.

    :param do_not_store: If the result of `get` is equal to this,
    it will not be stored. Defaults to None. If `get` returning None is
    desirable, set to a sentinel value.

    :param catch: Tuple of exceptions to catch. If `get` emits any other
    kind of error, it will not be caught. Defaults to catching all
    exceptions which obviously is not recommended. If an exception is
    caught, the store is not called.
    """
    if catch is None:
        catch = Exception
    value = do_not_store
    try:
        value = get()
    except catch:
        pass
    if value != do_not_store:
        store(value)


class BaseDatabaseDriver(ABC):
    @abstractmethod
    def __init__(self, location: str):
        pass

    @abstractmethod
    def create_tables(self) -> None:
        pass

    @abstractmethod
    def get_new_posts_for_user(
        self, user_id: str, search_timestamp: int
    ) -> TypedDict("NewPosts", {"thread_posts": List, "post_replies": List}):
        """Get new posts for the users with the given ID made since the
        given timestamp.

        Returns a dict containing the thread posts and the post replies.
        """
        pass

    @abstractmethod
    def store_user_config(self, user_config: UserConfig):
        """Caches a user notification configuration.

        :param user_config: Configuration for a user.
        """
        pass

    @abstractmethod
    def store_manual_sub(
        self, user_id: str, subscription: Subscription
    ) -> None:
        """Caches a single user subscription configuration.

        :param user_id: The numeric Wikidot ID of the user, as text.
        :param thread_id: Data for the subscription.
        """
        pass

    @abstractmethod
    def store_supported_site(
        self, sites: Dict[str, SupportedSiteConfig]
    ) -> None:
        """Stores a set of supported sites in the database, overwriting any
        that are already present."""
        pass


class DatabaseWithSqlFileCache(BaseDatabaseDriver, ABC):

    builtin_queries_dir = Path(__file__).parent / "queries"

    def clear_query_file_cache(self):
        """Clears the cache of query files, causing subsequent calls to
        them to re-read the query from the filesystem."""
        self.query_cache = {}

    def read_query_file(self, query_name: str) -> None:
        """Reads the contents of a query file from the filesystem and
        caches it."""
        try:
            query_path = next(
                path
                for path in self.builtin_queries_dir.iterdir()
                if path.name.split(".")[0] == query_name
            )
        except StopIteration:
            raise ValueError(f"Query {query_name} does not exist")
        with query_path.open() as file:
            query = file.read()
        self.query_cache[query_name] = {
            "script": query_path.name.endswith(".script.sql"),
            "query": query,
        }

    def execute_named(
        self, query_name: str, params: Iterable = None
    ) -> Cursor:
        """Execute a named query against the database. The query is read
        either from file or the cache.

        :param query_name: The name of the query to execute, which must
        have a corresponding SQL file.
        :param params: SQL parameters to pass to the query.
        :returns: The resultant cursor of the query.
        """
        if query_name not in self.query_cache:
            self.read_query_file(query_name)
        query = self.query_cache[query_name]["query"]
        if self.query_cache[query_name]["script"]:
            if params is not None:
                raise ValueError("Script does not accept params")
            return self.conn.executescript(query)
        if params is None:
            params = {}
        return self.conn.execute(query, params)


class SqliteDriver(DatabaseWithSqlFileCache):
    def __init__(self, location=":memory:"):
        self.conn = sqlite3.connect(location)
        self.conn.row_factory = sqlite3.Row
        self.clear_query_file_cache()
        self.execute_named("enable_foreign_keys")
        self.create_tables()

    def create_tables(self):
        self.execute_named("create_tables")

    def get_new_posts_for_user(
        self, user_id: str, search_timestamp: int
    ) -> TypedDict("NewPosts", {"thread_posts": List, "post_replies": List}):
        # Get new posts in subscribed threads
        thread_posts = self.execute_named(
            "get_posts_in_subscribed_threads",
            {"user_id": user_id, "search_timestamp": search_timestamp},
        ).fetchall()
        # Get new replies to subscribed posts
        post_replies = self.execute_named(
            "get_replies_to_subscribed_posts",
            {"user_id": user_id, "search_timestamp": search_timestamp},
        ).fetchall()
        # Remove duplicate posts - keep the ones that are post replies
        post_replies_ids = [post["id"] for post in post_replies]
        thread_posts = [
            thread_post
            for thread_post in thread_posts
            if thread_post["id"] not in post_replies_ids
        ]
        return {"thread_posts": thread_posts, "post_replies": post_replies}

    def store_user_config(self, user_config: UserConfig):
        user_id = user_config.user_id
        # TODO
        self.conn.commit()

    def store_manual_sub(
        self, user_id: str, subscription: Subscription
    ) -> None:
        self.execute_named(
            "store_manual_sub",
            {
                "user_id": user_id,
                "thread_id": subscription["thread_id"],
                "post_id": subscription["post_id"],
                "sub": subscription["sub"],
            },
        )

    def store_supported_site(
        self, sites: Dict[str, SupportedSiteConfig]
    ) -> None:
        # Destroy all existing wikis in preparation for overwrite
        self.execute_named("remove_all_wikis")
        # Add each new wiki
        for wiki_id, wiki in sites.items():
            self.execute_named(
                "add_wiki",
                {"wiki_id": wiki_id, "wiki_secure": wiki["secure"]},
            )
            for alt in wiki["alts"]:
                self.execute_named(
                    "add_wiki_alias",
                    {"wiki_id": wiki_id, "alias": alt},
                )
        self.conn.commit()


DatabaseDriver = SqliteDriver
