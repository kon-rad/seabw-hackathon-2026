# =========== Copyright 2023 @ CAMEL-AI.org. All Rights Reserved. ===========
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =========== Copyright 2023 @ CAMEL-AI.org. All Rights Reserved. ===========
"""Base classes for the Wonderwall simulation framework.

Every simulation type (social media, prediction market, auction house, etc.)
implements these interfaces. The core engine (WonderwallEnv, Channel, AgentGraph)
stays generic and delegates simulation-specific logic to these classes.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from camel.toolkits import FunctionTool

from wonderwall.clock.clock import Clock
from wonderwall.social_platform.channel import Channel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def create_db_from_schemas(
    db_path: str,
    schema_files: list[str],
    schema_dir: str,
) -> tuple[sqlite3.Connection, sqlite3.Cursor]:
    """Create a SQLite database and execute the given schema SQL files."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    for schema_file in schema_files:
        path = os.path.join(schema_dir, schema_file)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Schema file not found: {path}. "
                f"Available in {schema_dir}: {os.listdir(schema_dir)}"
            )
        with open(path, "r") as f:
            cursor.executescript(f.read())
    conn.commit()
    return conn, cursor


# ---------------------------------------------------------------------------
# BasePlatform — server-side action dispatch & DB lifecycle
# ---------------------------------------------------------------------------

class BasePlatform(ABC):
    """Abstract platform that runs the message loop and dispatches actions.

    Subclasses declare ``required_schemas`` and implement action handler
    methods.  The message loop uses ``getattr(self, action_name)`` to
    dispatch, so any ``async def my_action(self, agent_id, message)`` method
    automatically becomes a callable action.
    """

    # Subclasses list their SQL schema filenames here.
    required_schemas: list[str] = []

    # Schemas that every simulation gets (user registration, trace logging).
    core_schemas: list[str] = ["user.sql", "trace.sql"]

    # Path to the directory containing core shared schemas.
    _core_schema_dir: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "social_platform", "schema",
    )

    def __init__(
        self,
        db_path: str,
        channel: Any = None,
        sandbox_clock: Clock | None = None,
        start_time: datetime | None = None,
    ):
        self.db_path = db_path

        if sandbox_clock is None:
            sandbox_clock = Clock(60)
        if start_time is None:
            start_time = datetime.now()
        self.start_time = start_time
        self.sandbox_clock = sandbox_clock

        # Create DB with core schemas first, then simulation-specific ones.
        self.db, self.db_cursor = self._init_db()
        self.db.execute("PRAGMA synchronous = OFF")

        self.channel = channel or Channel()

    @property
    def schema_dir(self) -> str:
        """Path to this simulation's schema/ folder.

        By default, looks for a ``schema/`` directory next to the file that
        defines the concrete subclass.
        """
        return os.path.join(
            os.path.dirname(inspect.getfile(type(self))), "schema"
        )

    def _init_db(self) -> tuple[sqlite3.Connection, sqlite3.Cursor]:
        """Create the database with core + simulation-specific schemas."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Load core schemas from the shared location.
        for schema_file in self.core_schemas:
            path = os.path.join(self._core_schema_dir, schema_file)
            if os.path.exists(path):
                with open(path, "r") as f:
                    cursor.executescript(f.read())

        # Load simulation-specific schemas.
        sim_schema_dir = self.schema_dir
        for schema_file in self.required_schemas:
            path = os.path.join(sim_schema_dir, schema_file)
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"Schema file not found: {path}"
                )
            with open(path, "r") as f:
                cursor.executescript(f.read())

        conn.commit()
        return conn, cursor

    # ------------------------------------------------------------------
    # Message loop — generic, works for any simulation
    # ------------------------------------------------------------------

    async def running(self):
        """Main message loop. Dispatches actions via getattr."""
        while True:
            message_id, data = await self.channel.receive_from()

            agent_id, message, action = data

            # Allow both ActionType enums and plain strings.
            action_name = action.value if hasattr(action, "value") else action

            if action_name == "exit":
                if self.db_path == ":memory:":
                    dst = sqlite3.connect("mock.db")
                    with dst:
                        self.db.backup(dst)
                self.db_cursor.close()
                self.db.close()
                break

            action_function = getattr(self, action_name, None)
            if action_function:
                func_code = action_function.__code__
                param_names = func_code.co_varnames[:func_code.co_argcount]

                len_param_names = len(param_names)
                if len_param_names > 3:
                    raise ValueError(
                        f"Functions with {len_param_names} parameters are "
                        f"not supported."
                    )

                params: dict[str, Any] = {}
                if len_param_names >= 2:
                    params["agent_id"] = agent_id
                if len_param_names == 3:
                    second_param_name = param_names[2]
                    params[second_param_name] = message

                result = await action_function(**params)
                await self.channel.send_to((message_id, agent_id, result))
            else:
                raise ValueError(f"Action {action_name} is not supported")

    def run(self):
        asyncio.run(self.running())

    # ------------------------------------------------------------------
    # Core actions shared by all simulations
    # ------------------------------------------------------------------

    def _execute_db_command(self, command, args=(), commit=False):
        self.db_cursor.execute(command, args)
        if commit:
            self.db.commit()
        return self.db_cursor

    def _execute_many_db_command(self, command, args_list, commit=False):
        self.db_cursor.executemany(command, args_list)
        if commit:
            self.db.commit()
        return self.db_cursor

    def _record_trace(self, user_id, action, info, timestamp):
        """Record an action in the trace table."""
        import json
        trace_query = (
            "INSERT INTO trace (user_id, action, info, created_at) "
            "VALUES (?, ?, ?, ?)"
        )
        self._execute_db_command(
            trace_query,
            (user_id, action, json.dumps(info), timestamp),
            commit=True,
        )

    def get_current_time(self):
        """Get the current simulation time."""
        return self.sandbox_clock.time_transfer(
            datetime.now(), self.start_time
        )

    async def sign_up(self, agent_id, user_message):
        """Register a new agent on the platform."""
        user_name, name, bio = user_message
        current_time = self.get_current_time()
        try:
            user_insert_query = (
                "INSERT INTO user (user_id, agent_id, user_name, name, "
                "bio, created_at, num_followings, num_followers) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?)"
            )
            self._execute_db_command(
                user_insert_query,
                (agent_id, agent_id, user_name, name, bio, current_time, 0, 0),
                commit=True,
            )
            user_id = agent_id
            action_info = {
                "name": name, "user_name": user_name, "bio": bio
            }
            self._record_trace(user_id, "sign_up", action_info, current_time)
            return {"success": True, "user_id": user_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def do_nothing(self, agent_id: int):
        """Perform no action."""
        return {"success": True}

    async def update_rec_table(self):
        """Override in subclasses to implement content recommendation."""
        pass


# ---------------------------------------------------------------------------
# BaseAction — client-side action interface (LLM tool calling)
# ---------------------------------------------------------------------------

class BaseAction(ABC):
    """Agent-side action interface.

    Each public async method (except ``perform_action`` and ``sign_up``)
    becomes an LLM-callable tool.  Subclasses add domain-specific methods.
    """

    # Methods to exclude from auto-discovery as LLM tools.
    _excluded_methods: set[str] = {"perform_action", "sign_up",
                                   "get_openai_function_list"}

    def __init__(self, agent_id: int, channel: Channel):
        self.agent_id = agent_id
        self.channel = channel

    async def perform_action(self, message: Any, action_type: str):
        """Send an action through the channel to the platform."""
        message_id = await self.channel.write_to_receive_queue(
            (self.agent_id, message, action_type)
        )
        response = await self.channel.read_from_send_queue(message_id)
        return response[2]

    async def sign_up(self, user_name: str, name: str, bio: str):
        """Sign up this agent on the platform."""
        user_message = (user_name, name, bio)
        return await self.perform_action(user_message, "sign_up")

    async def do_nothing(self):
        """Skip this round and take no action. Choose this when the current
        market state already reflects your beliefs, no new information has
        emerged, or you want to conserve capital and wait for a better
        opportunity. Good traders and users are patient — not every round
        requires action. This should be your most common choice (30-50%
        of rounds).

        Returns:
            dict: {"success": True}
        """
        return await self.perform_action(None, "do_nothing")

    def get_openai_function_list(self) -> list[FunctionTool]:
        """Auto-discover all public async methods as LLM-callable tools."""
        tools = []
        for name in sorted(dir(self)):
            if name.startswith("_"):
                continue
            if name in self._excluded_methods:
                continue
            method = getattr(self, name)
            if asyncio.iscoroutinefunction(method):
                tools.append(FunctionTool(method))
        return tools


# ---------------------------------------------------------------------------
# BaseEnvironment — what the agent observes each turn
# ---------------------------------------------------------------------------

class BaseEnvironment(ABC):
    """Converts platform state into a text prompt for the agent's LLM."""

    def __init__(self, action: BaseAction):
        self.action = action
        # Optional extra context injected before each round (e.g., social media summary)
        self.extra_observation_context: str = ""

    @abstractmethod
    async def to_text_prompt(self) -> str:
        """Return a text description of what the agent currently observes."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# BasePromptBuilder — generates the agent's system prompt
# ---------------------------------------------------------------------------

class BasePromptBuilder(ABC):
    """Generates the system prompt that defines an agent's persona."""

    @abstractmethod
    def build_system_prompt(self, user_info) -> str:
        """Build the system prompt for an agent given its user info."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# SimulationConfig — declarative registration of a simulation type
# ---------------------------------------------------------------------------

@dataclass
class SimulationConfig:
    """Declarative definition of a simulation type.

    Bundle together the platform, action, environment, and prompt classes
    that define a simulation.  Pass this to ``wonderwall.make()`` to run it.
    """

    name: str
    platform_cls: type[BasePlatform]
    action_cls: type[BaseAction]
    environment_cls: type[BaseEnvironment]
    prompt_builder: BasePromptBuilder
    default_actions: list[str] = field(default_factory=list)
    platform_kwargs: dict[str, Any] = field(default_factory=dict)
