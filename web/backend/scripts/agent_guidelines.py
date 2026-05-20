"""Universal agent guidelines that apply to every agent in the simulation.

These are rules the runner injects into each agent's system message once,
at setup time. They persist for the lifetime of the agent (system messages
aren't rebuilt per-round), and they coexist with the per-round injections
— belief state, director events, counterfactuals — because they use a
distinct marker so the marker-replace logic in each injector doesn't
stomp on the others.
"""

from __future__ import annotations

_POSTING_RULES_MARKER = "\n\n# POSTING RULES"

# Plain-English rules the LLM will see at the tail of its system prompt.
# Keep short and imperative — the model is more reliable with terse rules
# than with rationalised ones.
POSTING_RULES_TEXT = (
    "When creating posts, comments, quotes, or replies:\n"
    "- Never use hashtags (no '#topic' style tags). Write naturally, as a "
    "real person would in conversation.\n"
)


def inject_posting_rules(agent, rules_text: str = POSTING_RULES_TEXT) -> None:
    """Append (or refresh) the universal posting rules on ``agent``.

    Uses the same marker-replace idiom as :func:`inject_director_event_context`
    so repeat calls don't stack copies of the block in the prompt.
    """
    content = agent.system_message.content

    marker_pos = content.find(_POSTING_RULES_MARKER)
    if marker_pos != -1:
        next_marker = content.find("\n\n# ", marker_pos + len(_POSTING_RULES_MARKER))
        if next_marker != -1:
            content = content[:marker_pos] + content[next_marker:]
        else:
            content = content[:marker_pos]

    agent.system_message.content = (
        content + _POSTING_RULES_MARKER + "\n" + rules_text
    )


def inject_posting_rules_into_graph(agent_graph, rules_text: str = POSTING_RULES_TEXT) -> int:
    """Apply :func:`inject_posting_rules` to every agent in *agent_graph*.

    Returns the number of agents updated. Best-effort — individual agents
    that can't be mutated (missing ``system_message``) are skipped silently.
    """
    if agent_graph is None:
        return 0
    count = 0
    for _, agent in agent_graph.get_agents():
        try:
            inject_posting_rules(agent, rules_text)
            count += 1
        except Exception:
            continue
    return count
