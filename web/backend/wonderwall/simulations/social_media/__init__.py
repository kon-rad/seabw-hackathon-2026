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
"""Social media simulation (Twitter / Reddit).

This is the original Wonderwall simulation type, now packaged as a
``SimulationConfig`` that can be used with the generic engine.
"""
from wonderwall.simulations.base import SimulationConfig
from wonderwall.simulations.social_media.prompts import (
    RedditPromptBuilder,
    TwitterPromptBuilder,
)

# Lazy imports to avoid circular dependencies — the heavy classes are only
# needed at runtime, not at import time for the config objects.


def _get_platform_cls():
    from wonderwall.social_platform.platform import Platform
    return Platform


def _get_action_cls():
    from wonderwall.social_agent.agent_action import SocialAction
    return SocialAction


def _get_environment_cls():
    from wonderwall.social_agent.agent_environment import SocialEnvironment
    return SocialEnvironment


twitter_simulation = SimulationConfig(
    name="twitter",
    platform_cls=_get_platform_cls(),
    action_cls=_get_action_cls(),
    environment_cls=_get_environment_cls(),
    prompt_builder=TwitterPromptBuilder(),
    default_actions=[
        "create_post", "like_post", "repost", "follow",
        "do_nothing", "quote_post",
    ],
    platform_kwargs={
        "recsys_type": "twhin-bert",
        "refresh_rec_post_count": 5,
        "max_rec_post_len": 5,
        "following_post_count": 5,
    },
)

reddit_simulation = SimulationConfig(
    name="reddit",
    platform_cls=_get_platform_cls(),
    action_cls=_get_action_cls(),
    environment_cls=_get_environment_cls(),
    prompt_builder=RedditPromptBuilder(),
    default_actions=[
        "like_post", "dislike_post", "create_post", "create_comment",
        "like_comment", "dislike_comment", "search_posts", "search_user",
        "trend", "refresh", "do_nothing", "follow", "mute",
    ],
    platform_kwargs={
        "recsys_type": "reddit",
        "allow_self_rating": True,
        "show_score": True,
        "max_rec_post_len": 100,
        "refresh_rec_post_count": 5,
    },
)

__all__ = [
    "twitter_simulation",
    "reddit_simulation",
    "TwitterPromptBuilder",
    "RedditPromptBuilder",
]
