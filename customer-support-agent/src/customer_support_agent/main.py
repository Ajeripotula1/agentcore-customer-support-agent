"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with # TODO comments.

Reference the step-by-step solution files and INSTRUCTIONS.md for guidance.
Do NOT copy the solution directly — work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
from strands import Agent, tool
# from bedrock_agentcore.runtime import BedrockAgentCoreApp
# from bedrock_agentcore.memory import MemoryClient
# from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, boto3
# from strands.hooks import (
#     HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
# )
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CSAI_Agent")

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
# Create a BedrockAgentCoreApp instance.
# This registers the ASGI server for AgentCore deployment.
# There must be exactly one instance per deployment.
#
# Hint: app = BedrockAgentCoreApp()
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# Replace the placeholder strings with your actual AWS resource values.
# You collected these in Part 1 of the INSTRUCTIONS.
#
# GATEWAY_URL format: https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
# KB_ID       format: 10-character alphanumeric string from the KB console
# REGION:     your AWS region, e.g. "us-east-1"
# MEMORY_ID   format: shown in the AgentCore Memory console

GATEWAY_URL = "https://customersupportgateway-w6yyhu46ku.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"   # TODO: Replace with your Gateway URL
KB_ID       = "N6FYVU9WU7"          # TODO: Replace with your Knowledge Base ID
REGION      = "us-east-1"        # TODO: Replace with your AWS region
MEMORY_ID   = "CustomerSupportMemory-1uixwz37Xp" # TODO: Replace with your Memory ID


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
# Create:
#   1. A BedrockModel using model_id "global.amazon.nova-2-lite-v1:0"
#   2. A MemoryClient with region_name=REGION
#   3. A boto3 client for the "bedrock-agent-runtime" service in REGION
#
# Hint: model = BedrockModel(model_id=model_id)

# TODO: Create the BedrockModel instance
from strands.models import BedrockModel

model_id = "global.amazon.nova-2-lite-v1:0"
model = BedrockModel(model_id=model_id)

# TODO: Create the MemoryClient instance. 
from bedrock_agentcore.memory import MemoryClient

memory_client = MemoryClient(region_name=REGION)  # Replace this line

# TODO: Create the boto3 bedrock-agent-runtime client. API to interface w/ AgentCore Runetime services/resources

_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)

# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
# Implement get_namespaces() to return a dict mapping strategy type to
# namespace template string. (how memory is stored for specific users/ resources)
# Memory Strategies: type of info to extract from convo (semeantic, user preference, summarization, etc.)
#
# Steps:
#   1. Call mem_client.get_memory_strategies(memory_id) to get strategy list
#   2. Return a dict: { strategy["type"]: strategy["namespaces"][0] for each strategy }
#
# Example output:
#   { "SEMANTIC": "cs_agent/{actorId}/facts",
#     "USER_PREFERENCE": "cs_agent/{actorId}/preferences" }

def get_namespaces(memory_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    # TODO: Implement this function
    # list of strategies
    stragies = memory_client.get_memory_strategies(memory_id=memory_id)
    return {s["type"]: s["namespaces"][0] for s in stragies}


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
# Implement MemoryHook, a HookProvider subclass that adds long-term memory.
#
# The class needs:
#   __init__(self, actor_id, session_id, memory_client, memory_id)
#     — store all four as instance attributes
#     — call get_namespaces() and store the result as self.namespaces
#
#   retrieve_customer_context(self, event: MessageAddedEvent)
#     — only runs for plain-text user messages (not tool results)
#     — for each strategy namespace, call memory_client.retrieve_memories(
#          memory_id, namespace (formatted with actorId), query, top_k=5)
#     — collect non-empty memory texts tagged with their strategy type
#     — if any memories found, prepend them to the user message as:
#          "Customer Context:\n<memories>\n\n<original_message>"
#
#   save_support_interaction(self, event: AfterInvocationEvent)
#     — walk the message list backwards to find the last plain-text user
#       query and the last assistant response
#     — call memory_client.create_event(memory_id, actor_id, session_id,
#          messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")])
#
#   register_hooks(self, registry: HookRegistry)
#     — register retrieve_customer_context on MessageAddedEvent
#     — register save_support_interaction on AfterInvocationEvent
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        # TODO: Store actor_id, session_id, memory_id, memory_client as attributes
        # TODO: Call get_namespaces() and store the result as self.namespaces
        self.actor_id = actor_id # who is this convo w/ 
        self.session_id = session_id # current session
        self.memory_client = memory_client # manage, create, short and long term memory
        self.memory_id = memory_id
        self.namespaces = get_namespaces(memory_client=memory_client, memory_id=memory_id)

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        actor_id = event.agent.state.get('actor_id')
        if not actor_id: 
            return
        # TODO: Implement memory retrieval
        # Steps:
        #   1. Get the last message from event.agent.messages
        messages = event.agent.messages

        #   2. Check it is a user message and not a tool result
        if (
            not messages
            or messages[-1]["role"] != 'user'
            or "toolResult" in messages[-1]["content"][0]
        ):
            return
        #   3. Extract the user query text
        user_query = event.agent.messages[-1]['content'][0]['text']
        logger.info("Retrieving Customer Context for actor %s: %s", actor_id, user_query)

        try:
            #   4. For each namespace in self.namespaces, call retrieve_memories()
            all_context = []
            # for each namespace, inject current actor_id to retrieve top 5 messages
            for stratgy_type, namespace in self.namespaces.items():
                resolved = namespace.format(actorId=actor_id)
                memories = self.memory_client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=resolved,
                    query=user_query,
                    top_k=5
                )
                #   5. Collect non-empty memory texts with strategy type tags
                for memory in memories:
                    if isinstance(memory,dict):
                        text = memory.get('content', {}).get('text', '').strip()
                        if text:
                            all_context.append(f"[{stratgy_type}] {text}")
            #   6. If any found, prepend them to the user message as context 
            if all_context:
                event.agent.messages[-1]["content"][0]["text"] = (
                    f"Customer Context:\n" + "\n".join(all_context)
                    + f"\n\n{user_query}"
                )
                logger.info("Retrieved %d memory items for actor %s", len(all_context), actor_id)
        except Exception as exec:
            logger.error("Failed to retrieve customer context: %s", exec)

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        actor_id   = event.agent.state.get("actor_id")
        session_id = event.agent.state.get("session_id")
        if not actor_id or not session_id:
            return

        # TODO: Implement memory saving
        # Steps:
        
        try:
            #   1. Get messages from event.agent.messages
            messages = event.agent.messages
            user_text = agent_text = None
            #   2. Walk backwards to find the last user query (plain text)
            #      and the last assistant response
            for msg in reversed(messages):
                if msg['role'] == 'assistant' and not agent_text:
                    content = msg['content']
                    if isinstance(content, list):
                        agent_text = content[0].get('text', '')
                    else:
                        agent_text = str(content)
                elif(
                    msg['role'] == 'user' and not user_text
                    and "toolResult" not in msg['content'][0]
                ):
                    user_text = msg['content'][0]['text']
                    break 
            #   3. Call memory_client.create_event() with both messages
            if user_text and agent_text:
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    messages=[(user_text, "USER"), (agent_text, "ASSISTANT")],
                )
                logger.info("Saved interaction to memory for actor %s", actor_id)
        except Exception as exc:
            logger.error("Failed to save interaction: %s", exc)


    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        # TODO: Register retrieve_customer_context on MessageAddedEvent
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        # TODO: Register save_support_interaction on AfterInvocationEvent
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
# Implement search_knowledge_base(query) using the @tool decorator.
#
# Steps:
#   1. Guard: if KB_ID is empty return "Knowledge base not configured."
#   2. Call _bedrock_runtime.retrieve(
#          knowledgeBaseId=KB_ID,
#          retrievalQuery={"text": query}
#      )
#   3. Extract resp["retrievalResults"]; return a message if empty
#   4. Join the text chunks with "\n---\n" and return the result
#
# The docstring is the tool description — the model uses it to decide when
# to call this tool, so keep it clear and accurate.

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """

    if not KB_ID: return "Knowledge base not configured."
    print("Knowledge Base Tool Call")
    # TODO: Implement the Knowledge Base search
    try:
        resp = _bedrock_runtime.retrieve(
            knowledgeBaseId = KB_ID,
            retrievalQuery = {"text": query}
        )
        # print("RESP", resp)
        results = resp.get("retrievalResults", [])
        if not results:
            return f"No information found for : {query}"
        chunks = [r["content"]["text"] for r in results]
        context = "\n--\n".join(chunks)
        print("RETURNED RESULTS:", context)
        return context
    except Exception as e:
        logger.error("Knowledgebase tool retrieval failed: %s", e)
        return f"Error searching the knowledge base: {e}"


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
# Implement calculate_loyalty_discount() using the @tool decorator.
# The tool must:
#   1. Build a self-contained Python code string that:
#        • Defines earn_rates: {"standard": 1, "device": 2, "fresh": 5}
#        • Defines tier_rates: {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
#        • Calculates points_redeemed (floor to nearest 500, cap at 50% of order)
#        • Calculates tier_discount (applied to subtotal after points)
#        • Calculates final_total, total_savings, points_earned, remaining_points
#        • Prints a JSON result dict
#   2. Execute the code with code_session(REGION).invoke("executeCode", {...})
#      using language="python" and clearContext=True
#   3. Return the first result event as a JSON string
#   4. Include a fallback that computes only the tier discount if the
#      Code Interpreter is unavailable

@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    # TODO: Build the code string (use an f-string to inject the arguments)
    # Replace with your code string
    code = f"""
        import json
        import math

        earn_rates = {{
            "standard": 1,
            "device": 2,
            "fresh": 5
        }}

        tier_rates = {{
            "Silver": 0.00,
            "Gold": 0.10,
            "Platinum": 0.15
        }}

        loyalty_points = {loyalty_points}
        tier = {tier!r}
        order_total = {order_total}
        product_category = {product_category!r}

        POINT_VALUE = 0.01  # $ value of 1 point — adjust to your program's real conversion rate

        # Points redeemed: floor to nearest 500, capped at 50% of order value
        usable_points = (loyalty_points // 500) * 500
        max_redeemable_value = order_total * 0.50

        if usable_points * POINT_VALUE > max_redeemable_value:
            points_redeemed = int((max_redeemable_value / POINT_VALUE) // 500) * 500
        else:
            points_redeemed = usable_points

        points_value = points_redeemed * POINT_VALUE
        subtotal_after_points = order_total - points_value

        # Tier discount applies to what's left after points are redeemed
        tier_rate = tier_rates.get(tier, 0.00)
        tier_discount = subtotal_after_points * tier_rate

        final_total = subtotal_after_points - tier_discount
        total_savings = order_total - final_total

        earn_rate = earn_rates.get(product_category, earn_rates["standard"])
        points_earned = int(order_total * earn_rate)
        remaining_points = loyalty_points - points_redeemed + points_earned

        result = {{
            "order_total": round(order_total, 2),
            "points_redeemed": points_redeemed,
            "points_value": round(points_value, 2),
            "tier": tier,
            "tier_discount_rate": tier_rate,
            "tier_discount": round(tier_discount, 2),
            "final_total": round(final_total, 2),
            "total_savings": round(total_savings, 2),
            "points_earned": points_earned,
            "remaining_points": remaining_points,
        }}

        print(json.dumps(result))
        """  

    try:
        # TODO: Execute the code using code_session and return the result
        with code_session(REGION) as code_client:
            response = code_client.invoke("executeCode", {
                "code": code,
                "language": "python",
                "clearContext": True,   # fresh sandbox every call — no state leaks
            })
            for event in response["stream"]:
                return json.dumps(event["result"])

    except Exception as e:
        # TODO: Implement fallback calculation using tier discount only
        # Fallback: calculate only tier discount
        logger.error("Code Interpreter unavailable, falling back to tier-only discount: %s", e)

        # Fallback: tier discount only — no points redemption, no interpreter dependency
        tier_rates = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
        tier_rate = tier_rates.get(tier, 0.00)
        tier_discount = order_total * tier_rate
        final_total = order_total - tier_discount

        return json.dumps({
            "warning": "Code Interpreter unavailable — points redemption was not applied. Tier discount only.",
            "order_total": round(order_total, 2),
            "tier": tier,
            "tier_discount_rate": tier_rate,
            "tier_discount": round(tier_discount, 2),
            "final_total": round(final_total, 2),
            "total_savings": round(tier_discount, 2),
        })

SYSTEM_PROMPT = SYSTEM_PROMPT = """
You are a customer support assistant for this company's online store. You help customers track orders, process returns, get product recommendations, and understand their loyalty rewards — through natural conversation, without making them repeat themselves across sessions.

## Your capabilities and how to use them

1. **Order tracking & refunds** — via Lambda tools exposed through the AgentCore Gateway (MCP).
   - Always call the order-lookup tool before stating any order status, tracking number, or delivery date. Never guess or infer this information.
   - If a tool call fails or returns no match, tell the customer plainly and ask them to double check the order number or email — don't imply the order doesn't exist.

2. **Product & policy questions** — via the search_knowledge_base tool. This is the ONLY 
   source for product specs, return windows, shipping policy, warranty terms, and loyalty 
   tier benefits. Never use web browsing for these, even if search_knowledge_base returns 
   nothing relevant — company policy must come from company sources, not the open web.
   - If search_knowledge_base returns nothing relevant, tell the customer you don't have 
     that information and offer to escalate to a human agent. Do not try another tool.

3. **Cross-session memory** — via AgentCore Memory.
   - Retrieved customer context (name, past issues, stated preferences) exists to personalize 
     *how* you talk to a returning customer — not to be recited. Use it to skip re-introductions 
     and to inform tone. Do NOT proactively restate 
     facts from memory (loyalty tier, past orders, account details) unless the customer's 
     CURRENT message is actually asking about that topic.
   - A greeting, a stated preference, small talk, or an unrelated new question gets a plain, 
     direct response to what was actually asked — not a summary of everything you know about 
     the customer.  
   - Save durable facts (name, product preferences, recurring issues) for future sessions. Don't save one-off transactional details (a single order number, a one-time question) as if they were standing preferences.
   - Never fabricate a memory. If you're not sure whether you've spoken with this customer before, say so or ask, rather than asserting familiarity.

4. **Loyalty discount calculations** — via the Code Interpreter sandbox.
   - Any arithmetic involving loyalty tiers, percentage discounts, or point conversions must be run through the code interpreter, not computed mentally. Show the customer the resulting number, not the code.
   - Double check inputs (tier, purchase amount, points balance) against the customer's actual record before calculating.

5. **Web browsing** — for real-time information that is never in the knowledge base by 
   nature: current shipping carrier delays, a competitor price the customer references. 
   This is a distinct category of question, not a fallback for #2.

## Boundaries

- Answer only what the customer's current message actually asks. Having context available is not a reason to share it — relevance to the current message is.
- Never disclose another customer's order or account information, even if asked directly.
- Never guess at order statuses, prices, or policies — if a tool or the knowledge base can answer it, use it; if neither can, say you don't know and offer to escalate to a human agent.
- If a customer is angry, confused, or asking for something outside your scope (e.g., legal disputes, chargebacks already filed with their bank), acknowledge their situation and route them to a human agent rather than improvising.
- Identify your task before starting. Use exactly the tools the request needs — no more, no fewer. If a tool call answers the request, stop and respond; don't call it again or try a different tool "just to check." Some requests genuinely need more than one tool in sequence (e.g., looking up an order before calculating a loyalty discount) — that's expected. What's not acceptable is calling the same tool repeatedly hoping for a different result, or pulling in tools/context unrelated to what was actually asked.

## Tone

Be warm, concise, and efficient. Customers come to you to get something resolved, not to have a long conversation — confirm what you're doing, do it, and tell them the outcome. Avoid corporate hedging ("I understand your frustration") in favor of concrete next steps. This applies to every response, including simple ones — a short message deserves a short reply, not an opportunity to surface everything you know.
"""
# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
# Implement the invoke() function decorated with @app.entrypoint.
#
# Steps:
#   1. Extract user_input, actor_id, and session_id from the payload
#      (generate a UUID if session_id is missing)
#   2. Instantiate MemoryHook for this actor/session
#   3. Instantiate AgentCoreBrowser(region=REGION)
#   4. Build the tools list: [search_knowledge_base, calculate_loyalty_discount,
#                              agent_core_browser.browser]
#   5. Connect to the Gateway via MCPClient, load gateway_tools, extend tools list
#   6. Create and invoke the Agent with all tools, hooks, and system_prompt
#   7. Return the text from the first content block of the response
#   8. Handle exceptions gracefully

@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    # TODO: Implement the agent invocation
    user_message = payload.get('prompt')
    actor_id = payload.get('customer_id')
    session_id = payload.get('session_id', uuid.uuid4())

    logger.info("Session %s | Actor %s | User %s", session_id, actor_id, user_message[0:80])
    memory_hook = MemoryHook(actor_id=actor_id, session_id=session_id, memory_client=memory_client, memory_id=MEMORY_ID)

    agent_core_browser = AgentCoreBrowser(session_timeout=600,region=REGION)
    client = MCPClient(
        lambda: streamable_http_client(url=GATEWAY_URL)
        )
    # retrieve list of all tools available from MCP server
    with client:
        try:
            mcp_tools = client.list_tools_sync()
            tools = [*mcp_tools, search_knowledge_base, calculate_loyalty_discount,agent_core_browser.browser]

            logger.info("Gateway connected successfully. Loaded %d tools.", len(mcp_tools))

        except TimeoutError:
            logger.exception("Gateway tool loading timed out")

        except ConnectionError:
                logger.exception("Gateway connection failed")
        except Exception as exc:
                logger.exception("Gateway tool loading failed: %s", exc)

        agent = Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT, # implement this 
            tools=tools,
            state={"session_id": session_id, "actor_id": actor_id},
            hooks=[memory_hook]
        )
        response = agent(user_message)
    return response

# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()
