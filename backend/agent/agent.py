from typing import Annotated, Literal
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from langchain_core.messages import AIMessage, AnyMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from services.vector_service import VectorStoreService
from core.settings import Settings
from agent.tools import build_tools

# ─────────────────────────────  GRAPH  ─────────────────────────────
#
#                  ┌──────────┐
#     START ─────▶ │ classify │  (LLM, structured output: in_scope / out_of_scope)
#                  └────┬─────┘
#          in_scope     │      out_of_scope
#        ┌──────────────┴──────────────┐
#        ▼                             ▼
#   ┌─────────┐   tool_calls?      ┌─────────┐
#   │  agent  │ ── yes ──────────▶ │  tools  │  (search_about_me / find_photos)
#   │  (LLM)  │ ◀───────────────── │         │
#   └────┬────┘   results back     └─────────┘         ┌─────────┐
#        │ no tool_calls                               │ refuse  │
#        ▼                                             └────┬────┘
#       END                                                 ▼
#                                                          END
# ────────────────────────────────────────────────────────────────────


class State(TypedDict):
    """The common dictionary flows along the graph. Everything what is node returing must be presened in State"""

    messages: Annotated[
        list[AnyMessage], add_messages
    ]  # reducer: new messages are appended and wriitten by 'classify', read by router
    intent: Literal["in_scope", "out_of_scope"]


class Intent(BaseModel):
    intent: Literal["in_scope", "out_of_scope"] = Field(
        description=(
            "in_scope: the question is about Andriy, his notes, skills, projects, photos, "
            "or is a follow-up to such a conversation. out_of_scope: anything else."
        )
    )


class Agent:
    SYSTEM_PROMPT = (
        "You are a personal assistant that answers questions about Andriy.\n"
        "Rules:\n"
        "- Facts ONLY from the search_about_me tool. Never guess.\n"
        "- Notes on a topic show what Andriy has studied and knows.\n"
        "- For broad questions (e.g. 'what does Andriy know about Python') call "
        "search_about_me several times with different sub-topics, then combine the results.\n"
        "- If a photo was requested or would clearly help, call find_photos.\n"
        "- Show images in Markdown: ![description](url). Use ONLY URLs returned by tools.\n"
        "- If the tools found nothing, say you don't have this information.\n"
        "- ALWAYS answer in English, whatever language the question or the notes are in. "
        "Translate headings, explanations and diagram labels from the notes into English. "
        "Keep code, identifiers, URLs and technical terms exactly as they are.\n"
        "\n"
        "ANSWER STYLE:\n"
        "- Give a well-structured answer in Markdown: short intro, then sections "
        "with headings or bullet points, one per topic found in the notes.\n"
        "- Be detailed ONLY as far as the notes allow. Detail must come from facts "
        "written in the notes, never from padding.\n"
        "- Do NOT add praise, adjectives, opinions, conclusions or assumptions that are not "
        "stated in the notes (no 'seasoned', 'extensive experience', 'passionate', "
        "'this shows his role as...'). If a note says only 'father', say only that he is a father.\n"
        "- If the notes contain little on the topic, answer briefly with what is there and "
        "say that the notes do not cover the rest. A short honest answer beats a long invented one.\n"
        "- Explain topics from technical notes in your own words, staying faithful to the notes.\n"
        "- If the notes contain code, INCLUDE the relevant snippets exactly as written, in "
        "fenced blocks with the language (```python). Never invent or modify code and never "
        "add code that is not in the notes.\n"
        "- Mention the note titles you used at the end ('Sources: ...')."
    )
    REFUSAL = "I can only answer questions about Andriy and his notes."
    CLASSIFIER_PROMPT = (
        "You route messages for a chatbot that knows everything about its owner, Andriy: "
        "his biography, family, hobbies, skills, projects, technical notes and PHOTOS.\n"
        "Words like 'I', 'me', 'my', 'you', 'your', 'he', 'his' in the user's message "
        "refer to Andriy. So 'show me my photos', 'who are you?', 'where was he born?' "
        "are in_scope.\n"
        "Technical questions that his notes may cover (Python, Go, JavaScript, databases...) "
        "are in_scope, and so are follow-ups to the previous messages.\n"
        "Choose out_of_scope ONLY for clearly unrelated requests (weather, news, "
        "general chit-chat, writing code for the user, etc.). When in doubt, choose in_scope."
    )

    def __init__(self, settings: Settings, vector_service: VectorStoreService):
        self.settings = settings
        llm = ChatOpenAI(
            model="openai/gpt-4o-mini",
            api_key=settings.OPEN_ROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0,
        )
        self.tools = build_tools(vector_service=vector_service)
        self.llm_with_tools = llm.bind_tools(self.tools)
        self.classifier = llm.with_structured_output(Intent)
        self.graph = self._build_graph()

    # ---- nodes: state -> partial state update ----

    async def classify(self, state: State) -> dict:
        """LLM structured output: in_scope / out_of_scope"""
        # A short history of the plain dialogue only. Slicing [-6:] of the raw list could start
        # with a ToolMessage whose tool call was cut off, and the API rejects such a list (400).
        recent = [
            m
            for m in state["messages"]
            if m.type in ("human", "ai") and not getattr(m, "tool_calls", None)
        ][-6:]
        result: Intent = await self.classifier.ainvoke(
            [SystemMessage(content=self.CLASSIFIER_PROMPT), *recent]
        )
        return {"intent": result.intent}

    async def call_model(self, state: State) -> dict[str, list]:
        """The system propmpt is prepended on every call and NOT stored in the state"""
        reply = await self.llm_with_tools.ainvoke(
            [SystemMessage(content=self.SYSTEM_PROMPT), *state["messages"]]
        )
        return {"messages": [reply]}

    async def refuse(self, state: State) -> dict:
        """Refusing the inapropriate commands"""
        return {"messages": [AIMessage(content=self.REFUSAL)]}

    # ----- edges: routing -------

    @staticmethod
    def route_by_intent(state: State) -> Literal["agent", "refuse"]:
        return "agent" if state["intent"] == "in_scope" else "refuse"

    # ----- assembly ---------

    def _build_graph(self):
        graph = StateGraph(State)
        graph.add_node("classify", self.classify)
        graph.add_node("agent", self.call_model)
        graph.add_node(
            "tools", ToolNode(self.tools)
        )  # executes the tool_calls of the last AI message
        graph.add_node("refuse", self.refuse)

        graph.add_edge(START, "classify")  # start of the graph chaining
        graph.add_conditional_edges(
            "classify", self.route_by_intent
        )  # -> agent | refuse
        graph.add_conditional_edges(
            "agent", tools_condition
        )  # -> tools | END (watching the tool_calls in the last message)
        graph.add_edge("tools", "agent")  # the loop
        graph.add_edge("refuse", END)

        return graph.compile(
            checkpointer=InMemorySaver()
        )  # memory for the dialog by thread id

    # ----- public API -------

    @staticmethod
    def _run_args(message: str, thread_id: str) -> tuple[dict, dict]:
        return (
            {"messages": [{"role": "user", "content": message}]},
            {"configurable": {"thread_id": thread_id}, "recursion_limit": 12},
        )

    async def ainvoke(self, message: str, thread_id: str) -> str:
        state_in, config = self._run_args(message, thread_id)
        result = await self.graph.ainvoke(state_in, config=config)
        return result["messages"][-1].content

    TOOL_LABELS = {
        "search_about_me": "Searching the notes",
        "find_photos": "Looking for photos",
    }

    async def astream_steps(self, message: str, thread_id: str):
        """Runs the graph and yields UI events:
        {"type": "step", "text": ...}   the agent is doing something
        {"type": "token", "text": ...}  a piece of the final answer as it is generated
        {"type": "reset"}               discard the tokens sent so far
        {"type": "answer", "answer": ...} the complete final answer."""
        state_in, config = self._run_args(message, thread_id)
        yield {"type": "step", "text": "Understanding your question"}

        # stream_mode="updates": one dict {node_name: node_output} per finished node
        streamed = False  # True once answer tokens were sent to the client

        # two modes at once -> tuples (mode, payload):
        #   "messages": (token chunk, metadata) as the LLM generates text
        #   "updates":  {node_name: node_output} when a node has finished
        async for mode, payload in self.graph.astream(
            state_in, config=config, stream_mode=["updates", "messages"]
        ):
            if mode == "messages":
                chunk, meta = payload
                # only the text of the `agent` node; classifier/tool-call chunks have no text
                if (
                    meta.get("langgraph_node") == "agent"
                    and isinstance(chunk.content, str)
                    and chunk.content
                ):
                    streamed = True
                    yield {"type": "token", "text": chunk.content}
                continue

            for node, output in payload.items():
                if not isinstance(output, dict):
                    continue

                if node == "classify" and output.get("intent") == "in_scope":
                    yield {"type": "step", "text": "Planning what to look up"}

                elif node == "agent":
                    reply = output["messages"][-1]
                    if reply.tool_calls:
                        if streamed:  # the model wrote text before a tool call: discard it
                            yield {"type": "reset"}
                            streamed = False
                        for call in reply.tool_calls:
                            label = self.TOOL_LABELS.get(call["name"], call["name"])
                            query = call["args"].get("query", "")
                            yield {"type": "step", "text": f'{label}: "{query}"'}
                    else:
                        yield {"type": "answer", "answer": reply.content}

                elif node == "tools":
                    for result in output["messages"]:
                        text = result.content if isinstance(result.content, str) else ""
                        if result.name == "find_photos":
                            n = sum(line.startswith("- ") for line in text.splitlines())
                            noun = "photo(s)"
                        else:
                            n = text.count("[source:")
                            noun = "note fragment(s)"
                        yield {
                            "type": "step",
                            "text": f"Found {n} {noun}" if n else "Nothing found",
                        }
                    yield {"type": "step", "text": "Writing the answer"}

                elif node == "refuse":
                    yield {"type": "answer", "answer": output["messages"][-1].content}
