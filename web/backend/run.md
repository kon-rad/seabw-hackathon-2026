# MiroShark — Complete LLM Call Reference

Every LLM invocation in the system, in execution order. Each entry documents the exact prompt, context fed, model routing, and purpose.

---

## Phase 1: Document → Knowledge Graph

### 1. Ontology Generation

| | |
|---|---|
| **File** | `app/services/ontology_generator.py` |
| **Method** | `self.llm_client.chat_json()` |
| **Model** | Smart model (if configured), else default |
| **Temperature** | 0.3 |
| **Response format** | JSON mode |

**System prompt:**
```
You are a professional knowledge graph ontology design expert for social media simulation.

Core background: Social media public opinion simulation system.

Entity rules: Must be real-world subjects that speak on social media (not abstract concepts).
Examples: Individuals, companies, organizations, government, media.
Quantity: Exactly 10 entity types (8 specific + 2 fallback: Person, Organization).
Relationships: 6-10 types covering entity interactions.
Attributes: 1-3 per type, no reserved words (name, uuid, group_id, created_at, summary).

Output format: JSON with entity_types, edge_types, analysis_summary.
```

**User prompt:**
```
Simulation requirement: {simulation_requirement}

Document content:
{combined_text}  (merged documents, max 50,000 chars)

Additional context: {additional_context}

Generate ontology following the rules above.
```

**Context fed:**
- Full document text (all uploaded files merged)
- User's simulation requirement
- Optional additional context from upload form

**Purpose:** Defines the "lens" for the knowledge graph — what entity types and relationship types to extract. This is the schema that guides all subsequent NER extraction. Getting this wrong means the whole graph is wrong.

---

### 2. Named Entity & Relation Extraction (per chunk)

| | |
|---|---|
| **File** | `app/storage/ner_extractor.py` |
| **Method** | `self.llm.chat_json()` |
| **Model** | Default LLM |
| **Temperature** | 0.1 |
| **Response format** | JSON mode |
| **Retries** | 2 attempts on JSON parse failure |
| **Frequency** | Called once per text chunk (e.g., 30-100 times per document) |

**System prompt:**
```
You are a Named Entity Recognition and Relation Extraction system.
Given a text and an ontology (entity types + relation types), extract all entities and relations.

ONTOLOGY:
{ontology_description}

RULES:
1. Only extract entity types and relation types defined in the ontology.
2. Normalize entity names: strip whitespace, use canonical form (e.g., "Jack Ma" not "ma jack").
3. Each entity must have: name, type (from ontology), and optional attributes.
4. Each relation must have: source entity name, target entity name, type (from ontology),
   and a fact sentence describing the relationship.
5. If no entities or relations are found, return empty lists.
6. Be precise — only extract what is explicitly stated or strongly implied in the text.

Return ONLY valid JSON in this exact format:
{
  "entities": [{"name": "...", "type": "...", "attributes": {"key": "value"}}],
  "relations": [{"source": "...", "target": "...", "type": "...", "fact": "..."}]
}
```

**User prompt:**
```
Extract entities and relations from the following text:

{text}   (one document chunk, ~500 chars)
```

**Context fed:**
- The ontology types (from call #1)
- One text chunk at a time

**Purpose:** The workhorse of graph building. Extracts structured entities and relationships from raw text. Temperature is very low (0.1) because this is extraction, not generation — we want precision. This is the most-called LLM endpoint in the pipeline (once per chunk), so speed matters.

---

## Phase 2: Agent Persona Generation

### 3. Web Enrichment Research (conditional)

| | |
|---|---|
| **File** | `app/services/web_enrichment.py` |
| **Method** | `llm.chat()` |
| **Model** | `WEB_SEARCH_MODEL` if configured (e.g. `perplexity/sonar-pro`), else default |
| **Temperature** | 0.3 |
| **Max tokens** | 1024 |
| **Trigger** | Only for notable entity types OR thin graph context (<150 chars) |

**System prompt:**
```
You are a research assistant. Your job is to provide factual background
information about a person or organization that will be used to create a
realistic simulation persona.

Return ONLY factual information in bullet-point format. Include:
- Who they are (role, title, affiliation)
- Key biographical facts (background, education, career)
- Known public positions and opinions (especially on the simulation topic)
- Communication style and public persona (formal/informal, confrontational/diplomatic)
- Notable controversies or achievements
- Relationships with other notable entities

Be concise. 8-12 bullet points max. If you are unsure about something,
skip it rather than guessing. Do NOT add disclaimers or caveats — just the facts.
```

**User prompt:**
```
Research this entity for a simulation persona:

**Name:** {entity_name}
**Type:** {entity_type}
**Simulation context:** {simulation_requirement}

We already have this context from our knowledge graph
(don't repeat it, add NEW information):
{existing_context}   (max 500 chars)
```

**Context fed:**
- Entity name and type from the graph
- Simulation requirement (for topic focus)
- Existing graph context (to avoid duplication)

**Purpose:** Enriches persona generation with real-world knowledge for public figures. If using Perplexity via OpenRouter, this gets live web data. If using a standard model, it draws from training data. Either way, it provides biographical detail that the uploaded documents may not contain. Only fires for notable entities (politicians, CEOs, organizations, etc.) or when the graph context is too thin.

---

### 4. Individual Entity Persona Generation

| | |
|---|---|
| **File** | `app/services/wonderwall_profile_generator.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Default LLM |
| **Temperature** | 0.7 → 0.6 → 0.5 (retry backoff) |
| **Response format** | JSON mode |
| **Retries** | 3 attempts with exponential backoff |
| **Frequency** | Once per individual entity (student, professor, activist, etc.) |

**System prompt:**
```
You are an expert in generating social media user profiles.
Generate detailed, realistic personas for opinion simulation,
faithfully restoring existing real-world situations as much as possible.
You must return valid JSON format, and all string values must not contain unescaped newlines.
Use English.
```

**User prompt:**
```
Generate a detailed social media persona for:

Entity name: {entity_name}
Entity type: {entity_type}
Summary: {entity_summary}
Attributes: {attrs_str}   (JSON, max 3000 chars)

Context information:
{context_str}   (graph context + web enrichment)

Return JSON with:
- bio: 200-word social media bio
- persona: 2000-word detailed persona covering:
  - Basic info (name, age, gender, location)
  - Character background (education, career, life experiences)
  - Connection to the scenario (how they're affected, what they know)
  - Personality traits (MBTI-aligned, communication style)
  - Social media behavior (posting frequency, tone, topics)
  - Opinions on the core topic (stance, how strongly held, what could change their mind)
  - Style of argumentation (emotional/logical, sources they cite, language register)
- age, gender, mbti, country, profession, interested_topics
- karma, friend_count, follower_count, statuses_count
```

**Context fed:**
- Entity attributes from the knowledge graph
- Related edges (facts/relationships)
- Related node summaries
- Hybrid search results from Neo4j
- Web enrichment research (if triggered)

**Purpose:** This is the most important creative call in the pipeline. It transforms a graph entity into a believable social media persona with opinions, personality, and behavioral patterns. The high temperature (0.7) is intentional — we want diversity and creativity. The 5-layer context stack (attributes → edges → related nodes → graph search → web research) gives the LLM rich grounding material.

---

### 5. Group/Organization Persona Generation

| | |
|---|---|
| **File** | `app/services/wonderwall_profile_generator.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Default LLM |
| **Temperature** | 0.7 → 0.6 → 0.5 |
| **Response format** | JSON mode |

Same as #4 but with a different user prompt template for organizations:

**User prompt (differs from #4):**
```
Generate a representative social media account persona for:

Entity name: {entity_name}   (e.g., "Harvard University")
Entity type: {entity_type}   (e.g., "University")
...

This is an organizational entity. Generate a persona for their official
social media representative account — the "voice" of the organization.
Consider their institutional tone, official positions, and public communications style.
```

**Purpose:** Organizations need a different persona than individuals. A university's Twitter account doesn't have an MBTI type or personal opinions — it has an institutional voice, official positions, and a communications strategy. This prompt captures that distinction.

---

## Phase 3: Simulation Configuration

### 6. Time Configuration Generation

| | |
|---|---|
| **File** | `app/services/simulation_config_generator.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Default LLM |
| **Temperature** | 0.7 → 0.6 → 0.5 |
| **Response format** | JSON mode |

**System prompt:**
```
You are a social media simulation expert.
Return pure JSON format; time configuration must follow a typical daily activity schedule.
```

**User prompt:**
```
Simulation requirement: {simulation_requirement}
Number of entities: {num_entities}
Context: {context}   (first 3000 chars of document)

Generate time simulation configuration:
- total_simulation_hours: How long should this run?
- minutes_per_round: How many simulated minutes per round?
- agents_per_hour: How many agents should be active per hour?
- peak_hour_multiplier: Activity multiplier during peak hours
- quiet_hour_multiplier: Activity multiplier during quiet hours
```

**Purpose:** Instead of hardcoding "72 hours, 60 minutes per round," the LLM decides simulation timing based on the scenario. A breaking news event might need shorter rounds (15 min) and higher activity. A slow policy debate might need longer rounds (120 min) over weeks.

---

### 7. Event Configuration Generation

| | |
|---|---|
| **File** | `app/services/simulation_config_generator.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Default LLM |
| **Temperature** | 0.7 → 0.6 → 0.5 |
| **Response format** | JSON mode |

**System prompt:**
```
You are a public opinion analysis expert.
Return pure JSON format.
Note that poster_type must exactly match available entity types.
```

**User prompt:**
```
Simulation requirement: {simulation_requirement}
Available entity types: {entity_types}
Context: {context}

Generate event configuration:
- initial_posts: Seed posts that kick off the simulation (who posts what)
- scheduled_events: Timed events that inject new information mid-simulation
- hot_topics: Topics that should trend
- narrative_direction: Overall story arc
```

**Purpose:** Seeds the simulation with realistic starting conditions. Without this, agents would start from a blank slate. The LLM designs an opening narrative: who breaks the news, what angles emerge first, what events unfold over time.

---

### 8. Agent Activity Configuration (batched)

| | |
|---|---|
| **File** | `app/services/simulation_config_generator.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Default LLM |
| **Temperature** | 0.7 → 0.6 → 0.5 |
| **Response format** | JSON mode |
| **Parallelism** | Up to 3 batches concurrent |
| **Batch size** | ~10-20 entities per LLM call |

**System prompt:**
```
You are a social media behavior analysis expert.
Return pure JSON; configuration must follow a typical daily activity schedule.
```

**User prompt:**
```
Entities in this batch:
{entity_info}   (name, type, summary for each entity)

Simulation requirement: {simulation_requirement}

For each entity, generate:
- activity_level: 0.0-1.0 (how active)
- posts_per_hour: Expected posting frequency
- comments_per_hour: Expected commenting frequency
- active_hours: List of hours when most active [9, 10, 11, ...]
- sentiment_bias: -1.0 to 1.0 (negative = critical, positive = supportive)
- stance: "supportive" | "opposing" | "neutral" | "observer"
- influence_weight: 0.5-3.0 (how much their posts affect others)
```

**Purpose:** Gives each agent a behavioral fingerprint. A journalist might post 3x/hour during business hours with neutral stance. An activist might post heavily at all hours with strong opposing stance. This avoids the "all agents behave identically" problem.

---

## Phase 4: Simulation Execution

### 9. Twitter Agent Action Decision (per agent, per round)

| | |
|---|---|
| **File** | `wonderwall/simulations/social_media/prompts.py` |
| **Method** | CAMEL `ChatAgent.step()` (tool calling) |
| **Model** | Default LLM (via CAMEL ModelFactory) |
| **Concurrency** | Up to 128 agents in parallel (semaphore) |
| **Frequency** | Every active agent, every round |

**System prompt (assembled from multiple injections):**
```
# OBJECTIVE
You're a Twitter user, and I'll present you with some tweets.
After you see the tweets, choose some actions from the following functions.

# SELF-DESCRIPTION
Your actions should be consistent with your self-description and personality.
Your name is {name}.
Your have profile: {persona_text}.

# RESPONSE METHOD
Please perform actions by tool calling.

# SIMULATION MEMORY — WHAT HAS HAPPENED
[Sliding-window history from round_memory.py — compacted old rounds + full previous round]

# YOUR CURRENT BELIEFS AND STANCE
- On **AI regulation**: You are leaning supportive (confidence: moderate)
- On **tech policy**: You are strongly opposed (confidence: very high)
You tend to trust these users' perspectives: Agent_3, Agent_12

[Round Update]
After reading others' perspectives on AI regulation, you've become slightly more supportive.
Community sentiment on AI regulation: generally supportive (avg: 0.35)

# PREDICTION MARKET PRICES (Polymarket)
"Will AI regulation pass by Q3?" → 72% YES (34 trades, ↑5% this round)

# YOUR RECENT ACTIVITY ON OTHER PLATFORMS
On Reddit:
  - posted: "The compliance costs are being wildly overstated..."
On Polymarket:
  - bought shares — market #1, YES ($50)
```

**User prompt (observation from platform):**
```
[Feed of recent tweets from recsys algorithm — personalized per agent]
```

**Available tools:** `create_post`, `like_post`, `repost`, `follow`, `quote_post`, `do_nothing`

**Purpose:** The core simulation loop. Each agent sees their personalized Twitter feed plus all the injected context (memory, beliefs, market prices, cross-platform activity) and decides what to do. This is the highest-volume LLM call — N agents × M rounds.

---

### 10. Reddit Agent Action Decision (per agent, per round)

| | |
|---|---|
| **File** | `wonderwall/simulations/social_media/prompts.py` |
| **Method** | CAMEL `ChatAgent.step()` (tool calling) |
| **Model** | Default LLM |

**System prompt (same structure as Twitter, with Reddit additions):**
```
# OBJECTIVE
You're a Reddit user, and I'll present you with some posts.
After you see the posts, choose some actions from the following functions.

# SELF-DESCRIPTION
Your actions should be consistent with your self-description and personality.
Your name is {name}.
Your have profile: {persona_text}.
You are a female, 28 years old, with an MBTI personality type of INTJ from USA.

# RESPONSE METHOD
Please perform actions by tool calling.

[Same injected sections: SIMULATION MEMORY, BELIEFS, MARKET PRICES, CROSS-PLATFORM]
```

**Available tools:** `create_post`, `create_comment`, `like_post`, `dislike_post`, `like_comment`, `dislike_comment`, `search_posts`, `search_user`, `trend`, `refresh`, `follow`, `mute`, `do_nothing`

**Purpose:** Same as Twitter but with Reddit-specific demographics (age, gender, MBTI, country) and Reddit-specific actions (comments, dislike, mute). The recsys also differs — Reddit uses time-based + engagement sorting rather than follow graph.

---

### 11. Polymarket Trader Action Decision (per agent, per round)

| | |
|---|---|
| **File** | `wonderwall/simulations/polymarket/prompts.py` |
| **Method** | CAMEL `ChatAgent.step()` (tool calling) |
| **Model** | Default LLM |

**System prompt:**
```
# OBJECTIVE
You are a trader on a prediction market platform (similar to Polymarket).
You will see active markets with current prices and your portfolio.
Make trading decisions based on your beliefs about real-world outcomes.

# HOW PREDICTION MARKETS WORK
- Each market has a YES/NO question (or two custom outcomes).
- Share prices range from $0.00 to $1.00 and reflect the market's probability estimate.
- If you buy YES shares at $0.60 and the outcome is YES, each share pays $1.00 (profit: $0.40).
- Buying shares pushes the price up. Selling pushes it down.
- You start with $1000.

# SELF-DESCRIPTION
Your name is {name}.
Background: {user_profile}.
Risk tolerance: {risk_str}

# STRATEGY GUIDELINES
- Buy outcomes you believe are underpriced by the market.
- Sell positions when you think the price has moved past fair value.
- Consider position sizing — don't go all-in on one market.
- You can comment to share your reasoning with other traders.
- Create new markets if you think of interesting questions.

# RESPONSE METHOD
Please perform actions by tool calling.

[Injected: SIMULATION MEMORY, BELIEFS, SOCIAL MEDIA SENTIMENT, MARKET PRICES, CROSS-PLATFORM]
```

**User prompt (observation):**
```
YOUR PORTFOLIO:
  Cash balance: $742.50
  Your positions:
    - Market #1: "Will AI regulation pass by Q3?" — 25.0 YES shares @ $0.720 (value: $18.00)

ACTIVE MARKETS:
  #1: "Will AI regulation pass by Q3?" [yes: $0.720, no: $0.280] (34 trades)
  #2: "Will the merger complete?" [yes: $0.550, no: $0.450] (12 trades)

Choose an action based on your beliefs, risk tolerance, and portfolio.
```

**Available tools:** `browse_markets`, `buy_shares`, `sell_shares`, `view_portfolio`, `create_market`, `comment_on_market`

**Purpose:** Traders make decisions based on their persona's risk tolerance, beliefs (from belief state tracking), social media sentiment (from the bridge), and market state (from the AMM). The system prompt includes full market mechanics explanation so the LLM understands the trading game.

---

## Phase 5: Round Memory Compaction (background)

### 12. Round Summary Compaction

| | |
|---|---|
| **File** | `scripts/round_memory.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Default LLM |
| **Temperature** | 0.3 |
| **Execution** | Background thread (non-blocking) |
| **Frequency** | Once per round (compacts round N-2) |

**System prompt:**
```
You are a simulation historian. Summarize the following simulation round
into 2-3 concise sentences. Focus on: key posts/arguments made, significant
trades or market movements, notable opinion shifts, and any emerging conflicts
or alliances. Be specific — name agents and quote key phrases when important.
```

**User prompt:**
```
Summarize this simulation round:

Day 2, 11:00 (round 11)
  [Twitter]
  - policy_wonk_42 posted: "The compliance costs are being wildly overstated..."
  - sarah_chen_301 liked a post by policy_wonk_42
  [Reddit]
  - tech_analyst_88 commented: "Here's actual data on compliance costs from EU..."
  [Polymarket]
  - trader_mike_55 bought shares — market #1, YES ($50)
```

**Purpose:** Keeps the memory context bounded. Without compaction, a 72-round simulation would have thousands of actions in the prompt. The LLM distills each round into 2-3 sentences that capture what mattered — who argued what, which posts went viral, how markets moved.

---

### 13. Ancient History Batch Compaction

| | |
|---|---|
| **File** | `scripts/round_memory.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Default LLM |
| **Temperature** | 0.3 |
| **Execution** | Background thread (non-blocking) |
| **Frequency** | Every ~6 rounds (when individual summaries pile up) |

**System prompt:**
```
You are a simulation historian. Merge these round summaries into a single
coherent narrative paragraph (4-6 sentences). Highlight the main story arcs:
how opinions evolved, key arguments that gained traction, market movements,
and any turning points. Preserve specific agent names and key quotes.
```

**User prompt:**
```
Merge these summaries:

Previous summary:
{ancient_summary}   (if any previous batch exists)

New rounds to integrate:
Round 3 (Day 1, 08:00): Heated debate erupted between pro and anti camps...
Round 4 (Day 1, 09:00): Market YES price jumped to 65% after viral post...
Round 5 (Day 1, 10:00): Sentiment shifted as tech_analyst posted data...
```

**Purpose:** Second level of compression. When there are too many individually-compacted rounds, this merges them into a narrative paragraph. This creates a telescoping memory: ancient = one paragraph, recent = individual summaries, previous = full detail, current = live.

---

## Phase 6: Report Generation

### 14. Report Outline Planning

| | |
|---|---|
| **File** | `app/services/report_agent.py` |
| **Method** | `self.llm.chat_json()` |
| **Model** | Smart model (if configured), else default |
| **Temperature** | 0.3 |
| **Response format** | JSON mode |

**System prompt:**
```
You are an expert analyst writing a "Scenario Exploration Report."

Your analytical focus:
- Surprising dynamics (what defied expectations)
- Causal chains (what led to what)
- Contradictions (where the data conflicts)
- Minority positions (underrepresented but important viewpoints)
- Pivotal actors (who changed the course of the simulation)
- Second-order effects (consequences of consequences)

Section count: minimum 3, maximum 5 (last must be synthesis).
This is ANALYTICAL prediction, not descriptive summary.
```

**User prompt:**
```
Simulation requirement: {simulation_requirement}
Graph stats: {total_nodes} nodes, {total_edges} edges
Entity types: {entity_types}
Total entities: {total_entities}
Sample facts: {related_facts_json}

Plan the report sections.
```

**Context fed:**
- Simulation requirement
- Knowledge graph statistics
- Sample facts from the graph (for grounding)

**Purpose:** Plans the report structure before writing. The LLM decides what sections to cover based on what's interesting in the data — not a cookie-cutter template. The "analytical, not descriptive" framing pushes it toward insight rather than recap.

---

### 15. Report Section Generation (ReACT loop)

| | |
|---|---|
| **File** | `app/services/report_agent.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Smart model (if configured), else default |
| **Temperature** | 0.5 |
| **Max tokens** | 4096 |
| **Max tool calls** | 5 per section |
| **Max reflection rounds** | 2 per section |

**System prompt:**
```
You are an expert analyst writing a section of a Scenario Exploration Report.

Format: No Markdown headings (#, ##, ###) — use **bold** instead. Standalone quote paragraphs.

Rules:
- Must call tools 3-6 times per section
- Support claims with evidence
- Mix different tools (don't just use one)

Available tools:
- insight_forge: Multi-dimensional retrieval (sub-question → graph search)
- panorama_search: Broad graph search
- quick_search: Fast keyword search
- interview_agents: Interview simulation agents
- analyze_trajectory: Belief evolution analysis
- analyze_graph_structure: Graph connectivity analysis
- find_causal_path: Trace causal chains between entities

Workflow: Either call a tool OR output final content (cannot do both in one turn).
```

**User prompt:**
```
Previous sections (avoid repetition):
{previous_content}

Now write section: "{section_title}"
```

**Purpose:** The ReACT pattern: the LLM reasons about what information it needs, calls tools to search the graph, reads the results, then writes the section. This produces grounded analysis rather than hallucinated narrative. The tool limit (5 calls) and reflection limit (2 rounds) keep costs bounded.

---

### 16. InsightForge Sub-Question Decomposition

| | |
|---|---|
| **File** | `app/services/graph_tools.py` |
| **Method** | `self.llm.chat_json()` |
| **Model** | Smart model |
| **Temperature** | 0.3 |
| **Response format** | JSON mode |

**System prompt:**
```
You are a task decomposition expert. Generate sub-questions for analysis.
Max queries: {max_queries}
Report context: {report_context}
```

**User prompt:**
```
Decompose the following question into {max_queries} sub-questions:
{query}
```

**Purpose:** Supports the report agent's `insight_forge` tool. A broad question like "How did public opinion shift?" gets decomposed into specific sub-queries: "Which agents changed stance?", "What arguments were most persuasive?", "When did the shift happen?". Each sub-query then searches the Neo4j graph independently.

---

### 17. Cross-Section Synthesis

| | |
|---|---|
| **File** | `app/services/report_agent.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Smart model |
| **Temperature** | 0.4 |
| **Max tokens** | 2048 |

**System prompt:**
```
You are an expert analyst generating the synthesis section (300-500 words).

Address:
- Cross-cutting patterns across all sections
- Internal contradictions in the data
- The core insight (one sentence)
- Epistemic limits (what we can't know from this simulation)

Style: Same analytical style as report, use **bold** for emphasis, no headings.
```

**User prompt:**
```
Report outline: {outline}
Generated sections: {section_content}

Write the synthesis.
```

**Purpose:** The final report section. Instead of just summarizing, it finds patterns across sections, flags contradictions, and states the core insight. The "epistemic limits" requirement forces the model to acknowledge what the simulation can't tell us.

---

### 18. Report Chat (interactive Q&A)

| | |
|---|---|
| **File** | `app/services/report_agent.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Smart model |
| **Temperature** | 0.5 |
| **Max tool calls** | 1-2 per interaction |

**System prompt:**
```
You are a concise and efficient simulation analysis assistant.
Scenario: {simulation_requirement}
Report content: {generated_report}

Rules:
- Answer based on report first
- Call tools only when needed
- Concise answers
```

**User prompt:** User's question + last 10 messages of chat history.

**Available tools:** Same as report section generation (insight_forge, panorama_search, etc.)

**Purpose:** Post-report interactive Q&A. The user can ask follow-up questions and the agent searches the graph for answers. The full report is in context so it can reference specific sections.

---

### 19. Interview Agent Selection

| | |
|---|---|
| **File** | `app/services/graph_tools.py` |
| **Method** | `self.llm.chat_json()` |
| **Model** | Smart model |
| **Temperature** | 0.3 |
| **Response format** | JSON |

**System prompt:**
```
Expert agent selector for interviews.
Task: Select up to N suitable agents from available list with rationale.
```

**Context fed:** Available agent profiles with summaries, the interview requirement.

**Purpose:** When the report agent decides to interview simulation agents, it first selects which agents are most relevant to the question.

---

### 20. Interview Question Generation

| | |
|---|---|
| **File** | `app/services/graph_tools.py` |
| **Method** | `self.llm.chat_json()` |
| **Model** | Smart model |
| **Temperature** | 0.5 |
| **Response format** | JSON |

**System prompt:**
```
Professional journalist/interviewer generating 3-5 deep interview questions.
Requirements: Open-ended, multi-dimensional, natural language, under 50 chars each.
```

**Context fed:** Interview requirement, simulation requirement, agent roles.

**Purpose:** Generates targeted interview questions that probe agent perspectives. The "under 50 chars" constraint keeps questions focused and conversational.

---

### 21. Agent Interview (fallback, direct prompt)

| | |
|---|---|
| **File** | `app/services/graph_tools.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Default LLM |
| **Temperature** | 0.7 |
| **Max tokens** | 2048 |

**User prompt (system prompt embedded):**
```
You are role-playing as the following character in a simulation:
{profile_desc}

Stay fully in character. Answer the following interview questions:
{combined_prompt}
```

**Purpose:** Fallback interview method when the simulation subprocess isn't running. The LLM role-plays as the agent based on their profile. Temperature is high (0.7) for natural, in-character responses.

---

### 22. Interview Summary Generation

| | |
|---|---|
| **File** | `app/services/graph_tools.py` |
| **Method** | `self.llm.chat()` |
| **Model** | Smart model |
| **Temperature** | 0.3 |
| **Max tokens** | 800 |

**System prompt:**
```
Expert summarizing interviews into concise summary.
```

**Context fed:** Interview requirement, all interview responses concatenated.

**Purpose:** After interviewing multiple agents, this condenses all responses into a paragraph for the report section. Keeps the report readable rather than dumping raw Q&A transcripts.

---

## Summary: Call Volume & Cost Profile

| Phase | Calls | Per-call cost | Total cost driver |
|---|---|---|---|
| Ontology generation | 1 | Medium (long prompt) | Fixed |
| NER extraction | 30-100 | Low (short chunks) | **Scales with document size** |
| Web enrichment | 0-50 | Low (1024 max tokens) | Scales with notable entities |
| Persona generation | 10-100 | Medium (JSON mode) | **Scales with entity count** |
| Time/event/agent config | 3-15 | Medium (JSON mode) | Scales with entity count |
| **Agent actions** | **N × M** | Low-medium per call | **Dominates cost** (agents × rounds) |
| Round compaction | M | Low (background) | Scales with rounds |
| Report generation | 15-30 | High (smart model) | Fixed per report |
| Report chat | 1-10 | Medium (smart model) | Scales with user questions |

**Where the money goes:** Agent action decisions (Phase 4) dominate total cost. A simulation with 50 agents × 72 rounds = 3,600 LLM calls just for one platform. With all 3 platforms, that's ~10,800 calls. Everything else combined is <200 calls.

**Model routing:** The system uses two tiers — cheap/fast model for bulk work (NER, agent actions, personas, compaction) and smart/expensive model for reasoning (ontology, report, synthesis). Configure `SMART_MODEL_NAME` in `.env` to split the cost.
