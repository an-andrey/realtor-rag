from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from .agent import create_landono_agent
from .config import DEFAULT_OPENAI_MODEL
from .contact import create_contact_token
from .env import load_project_env
from .handoff import create_broker_handoff, ensure_handoff_schema


WELCOME_MESSAGE = """Welcome to the Landono assistant sandbox.

You can ask about Landono listings by city, budget, buy/rent, bedrooms, features, or distance to landmarks
like McGill. I can recommend up to five matching listings, help narrow a search, or prepare a safe broker
handoff for buyers, renters, sellers, and landlords.

Lead capture is handled by /handoff so the model does not invent user phone numbers or emails.
"""


HELP_TEXT = """Commands:
  /help      Show commands
  /handoff   Capture contact details and create a broker handoff
  /quit      Exit

Anything else is sent to the assistant. OPENAI_API_KEY is loaded from .env at the project root.
"""


def main() -> None:
    load_project_env()
    parser = argparse.ArgumentParser(description="Landono assistant sandbox CLI")
    parser.add_argument("--model", default=os.getenv("LANDONO_OPENAI_MODEL") or DEFAULT_OPENAI_MODEL)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    ensure_handoff_schema()
    agent = None
    messages: list[dict[str, str]] = []

    print(WELCOME_MESSAGE)
    print(f"Model: {args.model}")
    print(HELP_TEXT)

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not user_input:
            continue
        if user_input in {"/quit", "/exit"}:
            return
        if user_input == "/help":
            print(HELP_TEXT)
            continue
        if user_input == "/handoff":
            run_handoff_capture()
            continue

        if "OPENAI_API_KEY" not in os.environ:
            print("assistant> Set OPENAI_API_KEY to chat with the model, or use /handoff to test lead capture.")
            continue

        if agent is None:
            agent = create_landono_agent(model=args.model, debug=args.debug)

        messages.append({"role": "user", "content": user_input})
        try:
            response = agent.invoke({"messages": messages})
        except Exception as exc:
            print(f"assistant> Agent call failed: {exc}")
            continue

        messages = _serializable_messages(response.get("messages", messages))
        answer = _last_assistant_text(messages)
        print(f"assistant> {answer}")


def run_handoff_capture() -> None:
    print("Creating a broker handoff. Contact info is captured directly by the CLI.")
    lead_type = _prompt_choice(
        "Lead type",
        {
            "1": "buyer",
            "2": "renter",
            "3": "seller",
            "4": "landlord",
            "5": "undecided",
        },
        default="5",
    )
    intent = _intent_for_lead_type(lead_type)
    channel = _prompt("Channel", default="cli")
    centris_ids = _prompt("Centris IDs, comma separated if relevant", default="")
    message = _prompt_multiline(
        "Message for the agency/broker. Include listing interests or seller/landlord property details."
    )

    contact_name = _prompt("Client name")
    contact_email = _prompt("Client email", default="")
    contact_phone = _prompt("Client phone", default="")
    preferred = _prompt("Preferred contact method", default="")

    token_result = create_contact_token(
        contact_name=contact_name,
        contact_email=contact_email or None,
        contact_phone=contact_phone or None,
        preferred_contact_method=preferred or None,
        capture_source="cli",
    )
    if token_result["status"] != "ok":
        print(f"handoff> Contact validation failed: {token_result['errors']}")
        return

    handoff = create_broker_handoff(
        contact_token=token_result["contact_token"],
        message=message,
        intent=intent,
        lead_type=lead_type,
        channel=channel,
        centris_ids=[item.strip() for item in centris_ids.split(",") if item.strip()],
    )
    print(f"handoff> {handoff['status']} id={handoff.get('handoff_id')}")
    broker = handoff.get("broker")
    if broker:
        print(
            "handoff> Routed broker from DB: "
            f"{broker['name']} | {broker.get('phone') or 'no phone'} | {broker.get('email') or 'no email'}"
        )
    print(f"handoff> {handoff.get('delivery_note', handoff.get('message', ''))}")


def _intent_for_lead_type(lead_type: str) -> str:
    if lead_type == "seller":
        return "sell_property"
    if lead_type == "landlord":
        return "rent_out_property"
    return "broker_followup"


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{label}{suffix}: ").strip()
    if not value and default is not None:
        return default
    return value


def _prompt_multiline(label: str) -> str:
    print(label)
    print("Finish with an empty line.")
    lines: list[str] = []
    while True:
        line = sys.stdin.readline()
        if line == "":
            break
        line = line.rstrip("\n")
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _prompt_choice(label: str, choices: dict[str, str], default: str) -> str:
    print(label + ":")
    for key, value in choices.items():
        print(f"  {key}. {value}")
    selection = _prompt("Choose", default=default)
    return choices.get(selection, choices[default])


def _serializable_messages(messages: list[Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for message in messages:
        if isinstance(message, dict):
            role = message.get("role") or message.get("type") or "assistant"
            content = message.get("content") or ""
        else:
            role = getattr(message, "type", "assistant")
            content = getattr(message, "content", "")
        if isinstance(content, list):
            content = "\n".join(str(item) for item in content)
        result.append({"role": role, "content": str(content)})
    return result


def _last_assistant_text(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message["role"] in {"assistant", "ai"} and message["content"]:
            return message["content"]
    return "(no assistant response)"


if __name__ == "__main__":
    main()
