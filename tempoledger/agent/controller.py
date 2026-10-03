"""AI agent controller for event-driven scheduling automation."""

import json
import logging
from typing import Any

from tempoledger.exceptions import AgentError
from tempoledger.models.events import EventType, SchedulingEvent
from tempoledger.models.sessions import SessionContext
from tempoledger.services.telemetry import telemetry

logger = logging.getLogger(__name__)


class AgentAction:
    """Represents an action taken by the agent."""

    def __init__(
        self,
        decisions: list[dict[str, Any]],
        reasoning: str,
        tools_called: list[str] | None = None,
    ):
        """
        Initialize agent action.

        Args:
            decisions: List of decisions made
            reasoning: Reasoning for the decisions
            tools_called: Optional list of tools called
        """
        self.decisions = decisions
        self.reasoning = reasoning
        self.tools_called = tools_called or []


class SchedulingAgent:
    """
    Event-to-proposal controller with optional external decision generation.

    Tool handlers only log. No scheduler, queue or session state is mutated.
    Session memory is an injected interface, not a bundled Redis implementation.
    """

    def __init__(self, memory=None):
        """
        Initialize scheduling agent.

        Args:
            memory: Optional memory service (for session context)
        """
        self.memory = memory
        self.tools = {
            "adjust_schedule": self._adjust_schedule,
            "reschedule_queue": self._reschedule_queue,
            "pause_session": self._pause_session,
            "alert_operator": self._alert_operator,
        }

    async def handle_event(
        self, event: SchedulingEvent, context: SessionContext | None = None
    ) -> AgentAction:
        """
        Route event to appropriate handler and decide action.

        Args:
            event: The scheduling event to handle
            context: Optional session context

        Returns:
            AgentAction with decisions and reasoning

        Raises:
            AgentError: If event handling fails
        """
        with telemetry.tracer.start_as_current_span(
            f"agent.handle.{event.event_type.value}"
        ) as span:
            try:
                # Get or create context
                if not context and self.memory:
                    context = await self.memory.get_session_context(event.session_id)
                elif not context:
                    # Create minimal context if no memory
                    context = SessionContext(
                        session_id=event.session_id,
                        campaign_name="unknown",
                        total_messages=0,
                        pattern_health=None,  # Will be set by handler
                    )

                # Build decision prompt
                prompt = self._build_decision_prompt(event, context)

                # Make decision (simplified - in production would call LLM)
                response = await self._make_decision(event, context, prompt)

                # Execute tool calls if any
                decisions = []
                tools_called = []
                for tool_call in response.get("tool_calls", []):
                    tool_name = tool_call.get("name")
                    if tool_name in self.tools:
                        result = await self.tools[tool_name](event, tool_call.get("args", {}))
                        decisions.append(result)
                        tools_called.append(tool_name)

                # Update memory with outcome
                if self.memory:
                    await self.memory.update_session(
                        event.session_id,
                        {
                            "last_event": event.model_dump(),
                            "last_action": {
                                "decisions": decisions,
                                "reasoning": response.get("reasoning", ""),
                            },
                        },
                    )

                return AgentAction(
                    decisions=decisions,
                    reasoning=response.get("reasoning", ""),
                    tools_called=tools_called,
                )

            except Exception as e:
                logger.error(f"Agent error handling event: {e}", exc_info=True)
                span.record_exception(e)
                raise AgentError(f"Event handling failed: {e}") from e

    async def _make_decision(
        self, event: SchedulingEvent, context: SessionContext, prompt: str
    ) -> dict[str, Any]:
        """
        Make a decision based on the event and context.

        Uses OpenAI API if configured, otherwise falls back to simplified logic.

        Args:
            event: The scheduling event
            context: Session context
            prompt: Decision prompt

        Returns:
            Dictionary with reasoning and tool_calls
        """
        from tempoledger.config import settings

        # Try OpenAI API if configured
        if settings.enable_live_agent and settings.openai_api_key:
            try:
                return await self._make_decision_with_openai(prompt, event)
            except Exception as e:
                logger.warning(f"OpenAI API call failed, using fallback: {e}")
                # Fall through to simplified logic

        # Simplified decision logic (fallback)
        tool_calls = []
        reasoning = ""

        if event.event_type == EventType.OPERATOR_FLAGGED:
            # Critical: pause and alert
            tool_calls.append(
                {
                    "name": "pause_session",
                    "args": {
                        "session_id": str(event.session_id),
                        "reason": "Operator flag received",
                    },
                }
            )
            tool_calls.append(
                {
                    "name": "alert_operator",
                    "args": {
                        "session_id": str(event.session_id),
                        "severity": "critical",
                        "message": "Operator flag received - immediate attention required",
                    },
                }
            )
            reasoning = (
                "Operator flag received. Logging pause and alert proposals; no state changed."
            )

        elif event.event_type == EventType.PATTERN_ANOMALY:
            # Warning: adjust schedule
            tool_calls.append(
                {
                    "name": "adjust_schedule",
                    "args": {
                        "session_id": str(event.session_id),
                        "parameter": "jitter_variance",
                        "value": 1.5,
                        "reason": "Pattern anomaly detected - increasing variance",
                    },
                }
            )
            reasoning = "Pattern anomaly detected. Logging parameter proposal; no state changed."

        elif event.event_type == EventType.REPLY_RECEIVED:
            # Adapt: reschedule queue with buffer
            tool_calls.append(
                {
                    "name": "reschedule_queue",
                    "args": {
                        "session_id": str(event.session_id),
                        "strategy": "pause",
                        "buffer_minutes": 15,
                    },
                }
            )
            reasoning = "Reply received. Logging buffer proposal; queue unchanged."

        elif event.event_type == EventType.DELIVERY_CONFIRMED:
            # Normal flow - continue
            reasoning = "Delivery confirmed. Continuing normal operation."

        else:
            # Default: continue
            reasoning = f"Event {event.event_type.value} processed. Continuing normal operation."

        return {"reasoning": reasoning, "tool_calls": tool_calls}

    async def _make_decision_with_openai(
        self, prompt: str, event: SchedulingEvent
    ) -> dict[str, Any]:
        """
        Make decision using OpenAI API with function calling.

        Args:
            prompt: Decision prompt
            event: The scheduling event

        Returns:
            Dictionary with reasoning and tool_calls
        """
        from tempoledger.config import settings

        if not settings.enable_live_agent:
            raise AgentError("Live decision generation is disabled")
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)

        # Define available tools as OpenAI function definitions
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "adjust_schedule",
                    "description": "Log a timing parameter proposal; no parameter changes",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "parameter": {
                                "type": "string",
                                "description": "Parameter name to adjust (e.g., 'jitter_variance', 'wpm_mean', 'pause_probability')",
                            },
                            "value": {
                                "type": "number",
                                "description": "New value for the parameter",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Reason for the adjustment",
                            },
                        },
                        "required": ["parameter", "value", "reason"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "reschedule_queue",
                    "description": "Log a queue replanning proposal; no queue changes",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "strategy": {
                                "type": "string",
                                "enum": ["delay", "pause", "spread"],
                                "description": "Rescheduling strategy",
                            },
                            "buffer_minutes": {
                                "type": "integer",
                                "description": "Buffer time in minutes",
                            },
                        },
                        "required": ["strategy"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pause_session",
                    "description": "Log a pause proposal; does not halt sending",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {
                                "type": "string",
                                "description": "Reason for pausing",
                            },
                        },
                        "required": ["reason"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "alert_operator",
                    "description": "Log an operator alert; no notification channel",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "severity": {
                                "type": "string",
                                "enum": ["low", "medium", "high", "critical"],
                                "description": "Alert severity",
                            },
                            "message": {
                                "type": "string",
                                "description": "Alert message",
                            },
                        },
                        "required": ["severity", "message"],
                    },
                },
            },
        ]

        try:
            response = await client.chat.completions.create(
                model="gpt-4o",  # or "gpt-4-turbo" or "gpt-3.5-turbo"
                messages=[
                    {
                        "role": "system",
                        "content": "Propose workload timing changes. All tools only log proposals; none can mutate a queue, parameters or delivery state.",
                    },
                    {"role": "user", "content": prompt},
                ],
                tools=tools,
                tool_choice="auto",
                temperature=0.3,  # Low temperature for consistent decisions
            )

            message = response.choices[0].message
            reasoning = message.content or "Decision made based on event analysis."

            # Parse tool calls from OpenAI response
            tool_calls = []
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    import json

                    tool_calls.append(
                        {
                            "name": tool_call.function.name,
                            "args": json.loads(tool_call.function.arguments),
                        }
                    )

            return {"reasoning": reasoning, "tool_calls": tool_calls}

        except Exception as e:
            logger.error(f"OpenAI API error: {e}", exc_info=True)
            raise

    def _build_decision_prompt(self, event: SchedulingEvent, context: SessionContext) -> str:
        """
        Construct prompt for LLM decision-making.

        Args:
            event: The scheduling event
            context: Session context

        Returns:
            Decision prompt string
        """
        return f"""
You propose workload scheduling changes. All tools are log-only stubs.
No tool can change parameters, reschedule a queue or pause delivery.

Current Event: {event.event_type.value}
Session: {context.campaign_name}
Messages Sent: {context.messages_sent} / {context.total_messages}
Recent Pattern Metrics: {context.pattern_health.model_dump() if context.pattern_health else "N/A"}

Event Details:
{json.dumps(event.metadata, indent=2)}

Available Tools:
- adjust_schedule: Log a timing parameter proposal
- reschedule_queue: Log a queue replanning proposal
- pause_session: Log a pause proposal (does not stop a worker)
- alert_operator: Log an operator alert (does not notify anyone)

Based on this event, what action should be taken? Consider:
1. Does this indicate operational anomalies risk?
2. Should timing parameters be adjusted?
3. Are the configured workload timing constraints in conflict?
4. Does this require human intervention?

Return your decision and reasoning.
"""

    async def _adjust_schedule(
        self, event: SchedulingEvent, args: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Log a parameter adjustment proposal; no scheduler mutation.

        Args:
            event: The scheduling event
            args: Tool arguments

        Returns:
            Tool result dictionary
        """
        logger.info(
            f"Log-only schedule proposal: {args}",
            extra={"session_id": str(event.session_id), "tool_args": args},
        )
        return {
            "success": False,
            "implemented": False,
            "logged": True,
            "action": "adjust_schedule",
            "message": "Proposal logged only; scheduling parameters unchanged",
        }

    async def _reschedule_queue(
        self, event: SchedulingEvent, args: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Log a queue replanning proposal; no queue mutation.

        Args:
            event: The scheduling event
            args: Tool arguments

        Returns:
            Tool result dictionary
        """
        logger.info(
            f"Log-only queue proposal: {args}",
            extra={"session_id": str(event.session_id), "tool_args": args},
        )
        return {
            "success": False,
            "implemented": False,
            "logged": True,
            "action": "reschedule_queue",
            "message": "Proposal logged only; queue unchanged",
        }

    async def _pause_session(self, event: SchedulingEvent, args: dict[str, Any]) -> dict[str, Any]:
        """
        Log a pause proposal; no session or worker mutation.

        Args:
            event: The scheduling event
            args: Tool arguments

        Returns:
            Tool result dictionary
        """
        logger.warning(
            f"Log-only pause proposal: {args}",
            extra={"session_id": str(event.session_id), "tool_args": args},
        )
        return {
            "success": False,
            "implemented": False,
            "logged": True,
            "action": "pause_session",
            "message": "Proposal logged only; session not paused",
        }

    async def _alert_operator(self, event: SchedulingEvent, args: dict[str, Any]) -> dict[str, Any]:
        """
        Log an alert proposal; no external notification.

        Args:
            event: The scheduling event
            args: Tool arguments

        Returns:
            Tool result dictionary
        """
        logger.critical(
            f"Log-only operator notice: {args}",
            extra={"session_id": str(event.session_id), "tool_args": args},
        )
        return {
            "success": False,
            "implemented": False,
            "logged": True,
            "action": "alert_operator",
            "message": "Alert logged only; no external notification sent",
        }
