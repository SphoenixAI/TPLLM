"""Agent chat example — use the LLM-powered TPM agent interactively.

Run with:
    ANTHROPIC_API_KEY=sk-... python examples/agent_chat.py

The agent has access to all TPM tools and will use them autonomously
to manage your program.
"""

from lltpm.config import LLTPMConfig
from lltpm.agent import TPMAgent


def main():
    config = LLTPMConfig.from_env()
    agent = TPMAgent(config)

    print("LLTPM Agent — Interactive Mode")
    print("=" * 40)
    print("Example prompts:")
    print('  "Set up a new program called Phoenix to rebuild our payments service"')
    print('  "Add a critical risk: third-party payment provider may change API"')
    print('  "Generate the daily brief for phoenix"')
    print('  "Should we have a meeting to discuss the database migration?"')
    print('  "Create a status update for the exec team"')
    print()

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break

        print()
        for turn in agent.run(user_input):
            if turn.tool_calls:
                for tc in turn.tool_calls:
                    print(f"  [tool: {tc['name']}]")
            if turn.text:
                print(f"\nLLTPM: {turn.text}")
        print()


if __name__ == "__main__":
    main()
